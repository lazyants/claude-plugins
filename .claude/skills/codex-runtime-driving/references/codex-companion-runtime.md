# Driving the codex-companion runtime & recovering its verdict

Mechanics for getting a reliable, retrievable Codex verdict out of the `codex:codex-rescue`
Agent / `codex-companion.mjs` runtime, and for diagnosing when a job backgrounds, hangs,
dies, or goes missing. (WHEN to reach for Codex vs. a parallel-review Workflow is a separate
standing guardrail — this file is the plumbing only.)

## Contents
- [Paths & locating the runtime](#paths--locating-the-runtime)
- [Fastest verdict recovery: read the job STATE JSON](#fastest-verdict-recovery-read-the-job-state-json)
- [Driving codex directly — the reliable patterns](#driving-codex-directly--the-reliable-patterns)
- [The rescue-Agent forwarder: behaviors & constraints](#the-rescue-agent-forwarder-behaviors--constraints)
- [Polling, monitoring & stall/hang detection](#polling-monitoring--stallhang-detection)
- [Finding a lost job / "No job found"](#finding-a-lost-job--no-job-found)
- [Session-boundedness & worker liveness](#session-boundedness--worker-liveness)
- [Environment breakages & diagnostics](#environment-breakages--diagnostics)
- [Verifying model/effort actually reached codex](#verifying-modeleffort-actually-reached-codex)
- [Driving long / parallel background jobs (driver correctness)](#driving-long--parallel-background-jobs-driver-correctness)
- [Consuming the verdict: adjudicate premises, beware stale reviews](#consuming-the-verdict-adjudicate-premises-beware-stale-reviews)
- [/security-review & working-tree diff caveats](#security-review--working-tree-diff-caveats)

## Paths & locating the runtime
- Companion script (version bumps — never hardcode `<ver>`):
  `/Users/moi/.claude-bm/plugins/cache/openai-codex/codex/<ver>/scripts/codex-companion.mjs`
  (also under `~/.claude/plugins/cache/...`). Resolve it with
  `find ~/.claude/plugins -iname "codex-companion.mjs"`.
- Job state dir keys to the **basename of the cwd the job ran in** — so a main-checkout run files
  under the repo dir name, and a **worktree run files under the WORKTREE dir name**:
  `~/.claude/plugins/data/codex-openai-codex/state/<cwd-basename>-<hash>/jobs/<task-id>.json` (+ `.log`).
  (Bake-off / other-profile variant: `~/.claude3/plugins/data/codex-openai-codex/state/<ws-hash>/jobs/<id>.json`.)
  **When a verdict goes missing, search BOTH** — an earlier version of this line claimed the dir
  never keys to the worktree, which sends you to the wrong directory on exactly the runs most likely
  to lose a verdict. Verified 2026-07-19: a review dispatched with the plan file inside
  `.claude/worktrees/eh-220-221` filed under `state/eh-220-221-<hash>/`, while same-session runs from
  the main checkout filed under `state/claude-plugins-<hash>/`; neighbouring dirs are likewise branch
  slugs (`645-A1-…`, `572-per-kit-tri-count-…`). Don't grep one dir and conclude the job never
  existed — `find ~/.claude*/plugins/data/codex-openai-codex/state -name '*.json' -mmin -120`.
- Codex config: `~/.codex/config.toml` (`model` default, `model_reasoning_effort`, `sandbox_mode`).
- Codex rollout transcript: `~/.codex/sessions/YYYY/MM/DD/rollout-<ISO>-<uuid>.jsonl`;
  `session_index.jsonl` lists thread names.
- Availability probe internals: `<companion-dir>/lib/codex.mjs`, function `getCodexAvailability`
  (calls `binaryAvailable` which probes `codex --version` + `codex app-server --help`).

## Fastest verdict recovery: read the job STATE JSON
The forwarder's detached `task-<id>` writes a sibling `<task-id>.json` next to the `.log`.
Read it directly — this beats resolving the version-bumped `codex-companion.mjs` + `status`/`result`:
```
jq -r '.result.rawOutput' <task-id>.json     # the FULL markdown verdict
```
Structured fields: `.status` (`running`→`completed`), `.pid` (liveness via `kill -0`),
`.request.effort` (confirm `unset` for plan/code review), `.write` (`false` for a review),
`.result.rawOutput` (full verdict) + `.rendered`.
Arm completion from a background Bash until-loop:
```
until [ "$(jq -r .status <json>)" != running ] || ! kill -0 <pid>; do sleep 15; done
```

## Driving codex directly — the reliable patterns
Driving the CLI/companion directly sidesteps ALL forwarder arg-tokenization and thread-state
wedges. Terminal job states are **`completed | failed | cancelled`** (NOT "succeeded"; NOT "running").

**A. Foreground `task` inside a `run_in_background:true` Bash — simplest for a plan/design-file review.**
```
node "$CC" task "$(cat prompt.txt)"        # $CC = codex-companion.mjs
```
The whole review runs foreground inside the backgrounded bash, so it survives tool-boundary
SIGTERMs, and the FULL verdict lands in the bash OUTPUT FILE on natural exit (+ a completion
notification) — no job-id, no `status`/`result` polling. Liveness signal differs: a foreground
`task` writes NO `~/.codex/sessions` rollout log and is NOT in `codex-companion status`, so
job-log/rollout mtime is useless — watch the **bash output file growing** and grep it for
`Turn completed`. A positional `"$(cat …)"` needs a **BACKTICK-FREE** prompt (backticks inside
`"$(…)"` trigger command substitution) — use `--prompt-file <p>` for arbitrary prompts.

**NEVER pipe the launcher through `tail`/`head`** — `node "$CC" task … | tail -80` buffers until
exit, so the bash output file sits at **0 bytes for the whole run**. That looks identical to "the
job never started", and acting on it costs more than the pipe ever saved. Verified 2026-07-19: a
`--prompt-file` review that was working perfectly read as dead, which triggered a kill + relaunch
+ a fabricated root cause (see the misdiagnosis warning below). Let it stream.

**Verify the prompt ACTUALLY ARRIVED — the launcher reports success either way.** Immediately after
launching, read the stored prompt back:
```
jq -r '.summary[0:120]' <job>.json     # must open with your real prompt text
```
Three distinct delivery bugs in one session all printed a cheerful "Codex Task started": an escaped
`"\$(cat …)"` (codex received the literal string `$(cat /path)`), an unquoted heredoc whose
backticks command-substituted (loud parse error — the benign case), and a prompt built in a shell
whose cwd had drifted. Only the summary read-back catches all three. "Job started" proves nothing
about what the job is reviewing.

**Misdiagnosis warning — do not infer a flag is unsupported from surface behavior.** `--prompt-file`
IS supported (`codex-companion.mjs:644`, `readTaskPrompt`; it is in `valueOptions` at `:764` and
takes precedence over positionals). `task --help` does NOT print help — `help` is not a known flag,
so it is dropped and codex runs with an empty prompt and answers with a generic capabilities blurb.
That is easily misread as "`--help` was treated as the prompt, therefore unknown flags become the
prompt, therefore `--prompt-file` isn't real". All three inferences are wrong. When runtime behavior
seems to contradict this reference, `grep` the companion source before rewriting either one — the
source is 30 seconds away and settles it.

**B. `--background` + poll — for long/parallel work you drive yourself.**
```
node "$CC" task --background --fresh --write --cwd <repo> --prompt-file <p> --json   # parse jobId
node "$CC" status <jobId>       # phase/elapsed/log path; poll until terminal
node "$CC" result <jobId>       # full verdict once phase leaves "running"
```
`task` opts (verified byte-identical across companion 1.0.4 and 1.0.6) are
`["json","write","resume-last","resume","fresh","background"]` — there is **NO `--wait` on `task`**
(unknown flags become harmless ignored positionals; `--prompt-file` wins over positionals in
`readTaskPrompt`). `--wait` exists ONLY on `review`/`adversarial-review`. `--prompt-file` also
dodges the forwarder misparsing prompt code/parens into a bogus `--model`/`--effort` flag.

**C. `codex exec` — direct CLI for a file/design review OUTSIDE the working tree.**
```
cd <root that contains ALL repos the review must read>
codex exec -s read-only -C <that-root> --skip-git-repo-check \
  -o verdict.txt - < review_prompt.txt   > full_log.txt 2>&1 &   # run_in_background
```
`-s read-only` = read+reason, no write (all a review needs); `-o FILE` captures ONLY the final
verdict (clean); `-C <root>` sets the readable root; `--skip-git-repo-check` allows a non-repo root.
Inline the plan text into the prompt (scratchpad may be outside `-C`). Read only the `-o` verdict
file, NOT the verbose log (session-memory noise there can trip a false prompt-injection warning).

**Reviewing a file that lives in the SCRATCHPAD — CHECK first, then stage only if the check fails.**
A fresh (non-resumed) codex session *may* be unable to see `/private/tmp/claude-501/…/scratchpad/`;
it then reports "could not locate the file" and reviews **whatever it can infer from your prompt
text instead** — a review that reads as authoritative but was never grounded in the artifact
(verified 2026-07-19: a plan-review round silently degraded this way and had to be re-run).
**But this is environment-dependent, NOT universal** — verified 2026-07-22 in a worktree run where
codex read the scratchpad plan directly and the job log showed its `wc -l <scratchpad-path>` at
`exit 0`, with round-1 findings correctly citing plan line numbers. So do not stage reflexively:
the staged copy is a real **staleness hazard** (every plan revision must be re-synced or codex
silently reviews an old draft, and you now have two copies to keep honest). Cheap check, ~2 s:
grep the job `.log` for your read of the file and its `exit 0` / `could not locate` line. Stage the
copy only if the read actually fails, and delete it once direct reads are confirmed working.
Fix, when you do need it — copy it in and git-exclude it:
```
/bin/cp -f <scratchpad>/plan.md <worktree>/PLAN-REVIEW-SCRATCH.md
echo "PLAN-REVIEW-SCRATCH.md" >> "$(git -C <worktree> rev-parse --path-format=absolute --git-path info/exclude)"
git -C <worktree> check-ignore -q PLAN-REVIEW-SCRATCH.md && echo excluded   # proves it, on any tree
```
Three traps in those three lines: **(a)** in a worktree `.git` is a FILE, not a directory, so a literal
`.git/info/exclude` redirect fails with `not a directory` — always resolve it via
`git rev-parse --git-path info/exclude`; **(b)** `cp` is aliased to `cp -i` in this shell and will sit
on an interactive overwrite prompt even with `-f`, so call `/bin/cp -f`; **(c)** `git -C <worktree>` on
BOTH git calls, AND `--path-format=absolute` on the `rev-parse` — bare `git` uses the shell's cwd,
which this same file documents as prone to drift. `-C` alone is not enough: in an ordinary checkout
`rev-parse --git-path` prints the RELATIVE `.git/info/exclude`, and the shell resolves the `>>`
redirect in ITS cwd, not the worktree's — so the exclude still lands in whatever repo you are standing
in (verified: a worktree returns an absolute path, an ordinary checkout does not). Verify with
`check-ignore` on the one path, not `git status --short` — status cannot be empty on an
already-dirty tree, so it proves nothing there. Delete the staged copy before
committing, and re-sync it on every plan revision or codex reviews a stale draft.
Smoke-test first: `printf 'reply PONG' | codex exec -s read-only -C <root> --skip-git-repo-check -o /tmp/o -`
should print `PONG`. For a broad working-tree review, SPLIT into N tightly-bounded parallel
`codex exec` runs (`run_in_background`), each with its own `-o`, rather than one broad Agent that backgrounds.

**D. `adversarial-review --scope working-tree "<focus>"`** — structured `Verdict:` + findings with file:line.
- ALWAYS pass a TIGHT focus string. No focus → it WANDERS (one ran 32 min emitting no final verdict);
  a focus that names the exact change + the 3–4 things to check + "END WITH A VERDICT LINE" converges ~5 min.
- `--scope working-tree` diffs `git diff HEAD`, so it MISSES untracked files. `git add -N <files>` makes
  them visible — but that intent-to-add then BLOCKS a scoped `git stash push -- <other>` ("Entry … not
  uptodate"); `git reset -- <files>` to undo it before any partial stash or the final commit.
- Run it via `--wait` inside a SINGLE `Bash(run_in_background:true)` call with a generous tool `timeout`
  and NO inner `timeout` wrapper — the full verdict lands in the captured output on natural exit.
- `--help` is NOT a thing here: any unknown flag is swallowed and it LAUNCHES a real review.
- Stage first: `git add <files>` (working-tree diff misses untracked). Tell it the repo uses Node's
  built-in test runner, NO jest (else it runs jest, gets exit 1, reports a bogus test failure).

**Never hand-roll `timeout N codex …` on macOS** — `timeout` isn't a builtin (it's `gtimeout` from
coreutils), so it fails `command not found`, codex never runs, and a trailing `echo`/`run_in_background`
reports a PHANTOM exit-0 "success" with no verdict file.

## The rescue-Agent forwarder: behaviors & constraints
- **Breadth gates backgrounding.** A *bounded* single-shot review (few named files + prompt says
  "run once and EXIT, report inline, do NOT open a streaming/background task") completes inline through
  the Agent and forwards its verdict. A *broad* multi-file review backgrounds → forwarder returns
  "started in background as task-<id>" and nothing useful is in the Agent result. Recovery: re-dispatch
  the SAME review tightly bounded.
- **The forwarder is a ONE-SHOT forwarder — it CANNOT poll.** Resuming it to poll fails ("barred from
  status/result/cancel"). Poll DIRECTLY from the main loop instead.
- **Idle-without-verdict after an INLINE run:** `SendMessage(to:<agent>, "reply now with your full verdict
  + every finding")` resumes it from transcript and it pastes the complete verdict. Do this FIRST;
  rollout-mining is a last resort (the "newest `rollout-*.jsonl`" grabs an UNRELATED concurrent codex session).
- **Slash commands `/codex:status` / `/codex:result` are `disable-model-invocation:true`** — a
  model-driven `Skill(codex:status,…)` errors. Translate to `node codex-companion.mjs status/result <id>`.
- **Duplicate-task guard:** a forwarder can spawn a SECOND identical codex task. After it returns,
  `grep '| rescue |'` in `status --all`; if two are `running`, cancel the redundant one (keep the
  further-along by log size: `stat -f %z <job>.log`).
- **Fabricated wait-state:** the forwarder can report "waiting for a background job" (or name a
  plausible-but-nonexistent job id) with NOTHING actually dispatched. Never trust a bare "waiting for
  background job" — resume it to actually poll/verify, and independently check `status --all` for a job
  whose STATE-DIR path matches your repo slug **or the basename of whatever cwd you launched from**.
- **Arg-misparse launch failure:** a prompt containing parenthesized code (e.g. `` (`python3 -m pytest`) ``)
  can make the forwarder build `--model "pytest),"` → codex 400 `… model is not supported`; `status <id>`
  shows `failed` in ~9 s. Fix = drive directly with `--prompt-file`.

## Polling, monitoring & stall/hang detection
- **The Bash background ID ≠ the Codex job id** (`review-…`/`task-…`). `result <bash-id>` → "No job found".
  Get the job id from `status --all`.
- **`result <job-id>` returns "No job found" while the job is still RUNNING** — `result` only works on
  FINISHED jobs; that is NOT an empty result.
- **Don't poll in a tight loop.** Wrap it in a `Bash(run_in_background:true)` until-loop so you get one
  completion notification: `until ! node "$CC" status <id> | grep -q "| running |"; do sleep 15; done`.
- **Never combine `&`/`disown` WITH `run_in_background:true` on the same poll loop — it double-backgrounds
  and the completion notification fires on the WRONG thing.** `Bash(run_in_background:true)` already tracks
  and notifies on ITS OWN command's completion; if that command is `(until …; done) & disown; echo started`,
  the tracked foreground command is the wrapper (which returns in milliseconds after launching the detached
  subshell), not the inner loop — so the harness reports "completed" seconds after launch while the real
  poll is still running untracked in the background, writing to a log file nobody gets notified about.
  Verified 2026-07-23: two consecutive false-"completed" notifications for a codex job that was still
  genuinely `running`/pid-alive minutes later; direct `jq`/`ps` checks caught it, the harness's own
  notification did not. Fix: write the poll loop as the DIRECT foreground command of the
  `run_in_background:true` call, with no trailing `&`/`disown` — `run_in_background` is the ONLY
  backgrounding mechanism needed; adding a second one breaks the first.
- **Key the watcher to the SPECIFIC task-id**, never "newest rescue task" (`grep rescue | tail -1`):
  completed tasks from prior rounds linger in `status` and a stale one fires a false "TERMINAL".
- **A harness-killed poller ≠ a dead review.** A `--wait` poll or a foreground `task` inside
  `run_in_background` can be harness-STOPPED mid-wait (status `killed` / "was stopped") while the detached
  codex WORKER keeps running — `status <id>` still shows `running` with a climbing Elapsed, and `.pid`
  is still alive via `kill -0`. Just re-arm the same until-loop / relaunch the same bounded prompt to a
  NEW output file (don't reuse the killed one).
- **Stall detection by log mtime.** Compare `stat -f %m <job>.log` (or the `~/.codex/sessions/**/rollout-*.jsonl`)
  to now: 120 s of flat log is NORMAL LLM reasoning; **silent > ~1500 s while the process is alive = dead
  stream, not thinking** (a real hang is 5–10 min flat; `verifying`-phase and `running`-phase broker stalls
  both look like this). A foreground-`task`-in-bash stall shows as output `< ~400 B` after ~7 min.
- **Cap the poller at a SOFT ~20 min** that EXITs with a "POSSIBLE HANG" line if still non-terminal,
  rather than an 80-min window — a frozen logFile mtime vs. `updatedAt` confirms it. Even a FRESH
  `task --background` can dead-stream at "Turn started" (`phase: starting`/`running`, frozen mtime).
- **Recovery for any wedge: `cancel <id>` + re-dispatch FRESH.** The RESUME path is what dies; a fresh,
  tightly-scoped dispatch completes. `task --resume-last` on a fat transcript times out at the companion's
  ~10-min ceiling (exit 143, no verdict) — never resume-last across rounds; start a new bounded task and
  re-state the small context it needs, scoped "answer these N questions, then STOP; do not background."
- Arm a `Monitor` stall-watchdog from the FIRST round — a fresh dispatch can stall silently at the very start.

## Finding a lost job / "No job found"
When the forwarder's prose "job ID" matches nothing under `status --all`:
- The job is filed under a state dir keyed on the **launching shell's cwd BASENAME** — so a job
  launched from a worktree files under the WORKTREE name, not the repo slug. Search every candidate
  dir, not just the repo one. Note the Bash tool's cwd PERSISTS between calls, so an earlier `cd`
  into a worktree silently redirects later launches (verified 2026-07-19: consecutive reviews in one
  session landed in `claude-plugins-*` and `eh-220-221-*` purely from a leftover `cd`).
  Recover it by session id
  (a UUID that appears in the forwarder's own Bash tool-call transcript even when the prose id is bogus):
  ```
  grep -rl "<codex-session-id>" ~/.claude/plugins/data/codex-openai-codex/state/
  ```
  or by dispatch time:
  ```
  find ~/.claude/plugins/data/codex-openai-codex/state -iname "*.json" -newermt "<dispatch time>"
  ```
  Then `node "$CC" status <real-task-id>` works.

## Session-boundedness & worker liveness
- The **forwarder's native detached `task-<id>` SURVIVES its own exit**, runs to completion, and is
  adoptable/pollable later — this is the *usual* case, but **not unconditional: verify liveness, do
  not assume it.** Counter-observation (2026-07-22, n=1, plan-review round 2): the harness
  BACKGROUNDED the `Agent(codex:codex-rescue)` call itself — the forwarder returned "manually
  backgrounded by the user … I don't monitor progress" and the codex WORKER died with it. Tell:
  `.status` still says `running`, but `kill -0 <pid>` is DEAD and the job `.log` ends mid-command,
  cold for minutes. Same signature as the `--background`-under-a-Bash-tool-`timeout` kill below, but
  a different trigger (the Agent call, not a Bash timeout), so the "detached ⇒ safe" reading does not
  cover it. **Always check `kill -0 .pid` + log mtime before believing `running`**; recovery is
  `cancel <id>` + a FRESH direct `--prompt-file` dispatch (pattern A), never a resume.
- A **codex arm spawned INSIDE a Workflow agent** (e.g. via the codex-cli-runtime wrapper) is
  SESSION-BOUND: it survives the Workflow subagent's ~2-min wrapper timeout WITHIN the session (poll
  `status`/`result`, or read the partial verdict from `…/jobs/<id>.log`), but after a full CC session
  RESTART `status <id>` returns **"No job found"**. Harvest it within the same session; don't plan to
  adopt it after a restart.
- **`--background` under a Bash-TOOL `timeout`** gets the worker SIGTERM-killed WITH the launcher: the
  job LOG goes cold the instant the launcher is killed (exit 143) while `status` still lies `running`
  with a climbing wall-clock Elapsed — this exactly mimics a rate-limit/verifying stall. Tell it apart:
  if the log went cold within seconds of a launcher `exit 143`, it's a self-inflicted kill (relaunch),
  not a codex problem. Avoid it by using pattern A or B above, never `--background` under a foreground
  Bash-tool timeout or an inner `timeout` wrapper.

## Environment breakages & diagnostics
Any of these can make codex-rescue *look* absent/broken when Codex is fine — diagnose, don't assume absence.
- **Subagent PATH false-negative** — the subagent reports "Codex CLI is not installed or is missing
  required runtime support." Its non-interactive shell lacks Apple-Silicon Homebrew's `/opt/homebrew/bin`,
  so the `codex --version` probe fails; an inline `export PATH=…` in a resume does NOT fix it (codex spawns
  in a separate process). Prove it's an ENV problem, not a real absence, from the MAIN shell:
  ```
  node -e "import('<companion-dir>/lib/codex.mjs').then(m=>console.log(m.getCodexAvailability(process.cwd())))"
  ```
  → `{available:true}` means the binary is fine; the failure is the subagent env. Drive from the main loop.
- **Shared-broker mid-run hang** — `status` shows `Session runtime: shared session` (another CC session
  started the broker); the job log goes flat for 5–14 min while Elapsed climbs (a `running`-phase stall).
- **`python3 -c` stdin-import hang** — codex import-tests a script via `python3 -c 'import importlib…'`;
  a house script that reads JSON from stdin BLOCKS forever. PREPEND every focus: "do NOT `python3 -c`
  import scripts under assets/scripts/ — they read stdin and hang; use pytest."
- **No writable TMPDIR in the codex sandbox** — the review runs clean but `pytest` inside its sandbox hits
  `FileNotFoundError: No usable temp directory`, forcing a "NOT SHIP" that reads like a code problem. Don't
  re-dispatch hoping for a temp dir — re-run the full suite yourself in the main loop on the same unmodified
  diff and treat a fresh green run as closing that gap.
- **Sandbox MODERATION block** — a security-flavored prompt (attacking a guard, decode bypass) trips a
  "flagged for possible cybersecurity risk" kill mid-run before any verdict. Reframe the SAME review in
  neutral terms ("ordinary software QA of a text-parsing routine," name the concrete files/functions, drop
  attacker/bypass framing) and it completes and still finds the real bugs.
- **Usage-limit block** — the Agent returns a plain "You've hit your usage limit … try again at <time>";
  no task starts, nothing to poll. It's a hard stop until the reset time (recovery is governed by the
  separate substitution guardrail, not this plumbing).

## Verifying model/effort actually reached codex
- **`status`/`result` do NOT record the effective model/effort** (foreground records + stored result
  payloads omit them). The BACKGROUND job REQUEST json DOES retain the *requested* values → verify an
  override reached codex by reading the job json's `.model` / `.effort` (or `.request.effort`), not `status`.
- **`--effort` whitelist = `none/minimal/low/medium/high/xhigh` ONLY.** Config-level `ultra`/`max` are NOT
  flag-passable (config-default only); passing `--effort max` explicitly THROWS `Unsupported reasoning
  effort` even though `max` works fine as the inherited `~/.codex/config.toml` default. Model: any string
  passes (only `spark` is aliased).

## Driving long / parallel background jobs (driver correctness)
For a long or fan-out `--background` job (multi-segment translation, N parallel reviews), a shipped
driver polling `status` is the reliable shape. The non-obvious correctness rules:
- **The broker is spawned `detached:true` into its OWN session**, so a parent process-group `killpg`
  CANNOT reach it on timeout — the only broker-aware kill is codex's own `cancel <jobId>`.
- **`cancel` does NOT prove the turn stopped.** `handleCancel` only best-effort `turn/interrupt`s
  (a NO-OP when threadId/turnId is missing) and `terminateProcessTree(NaN)` no-ops, then UNCONDITIONALLY
  writes status `cancelled`. "Left the active set" proves the METADATA flipped, not that the detached turn
  died. **Fix by ISOLATION, not proof-of-termination:** run each attempt in its own throwaway workspace
  (`.att/<token>/`, unique per launch), `--cwd` it there, and ATOMICALLY promote its output (`os.replace`)
  to the canonical path ONLY after observing `completed`; on timeout, best-effort cancel + DELETE the
  jobfile and ABANDON the dir. A surviving zombie can then only write into an abandoned dir.
- **Path isolation is a PRE-FILTER, not a quiescence guarantee.** Under one shared `--write`
  (`workspace-write`) root, a sibling job CAN write another seg's unique path, and an `O_NOFOLLOW`-opened
  fd does NOT bind a later by-PATH validate/`os.replace` to one inode. Rest consumption-safety on
  RE-VALIDATING the artifact's CONTENT at the accept gate (robust to any writer) — never on "I hold a
  lock / I used a unique path, therefore it's quiescent."
- **Restart-adoption of a still-`running` job MUST be bound to the exact INPUT.** Record
  `<jobid>\t<attdir>\t<sha(prompt)>` and adopt only if the recorded prompt-sha == sha(current prompt);
  otherwise a crash-restart regenerates a new input while the stale reviewer of the OLD input gets adopted
  and its verdict silently blessed against the wrong artifact.
- **Single-writer driver lock = `fcntl.flock(fd, LOCK_EX|LOCK_NB)` held for process lifetime, NOT a
  pid-file.** A pid-file (O_EXCL + liveness + rename-capture) is not compare-and-swap and double-acquires
  under a race; `flock` is kernel-atomic and auto-released on death — no stale-pid logic, no reclaim race.
- **Run the driver detached** so it survives tool-return and periodic bg-window kills:
  `nohup python3 driver.py > log 2>&1 < /dev/null & disown` from a normal foreground Bash call (`setsid`
  is absent on macOS). Make it RESUMABLE via hash-bound done-markers, and poll it with SHORT re-armed
  `run_in_background` windows.

## Consuming the verdict: adjudicate premises, beware stale reviews
- **Verify a finding's PREMISE before applying it.** Reviewers flag false positives (e.g. "`import yaml`
  is a stdlib-only blocker" when PyYAML is a declared dep and `yaml.safe_dump` is the injection-SAFE path).
  Check each finding's assumption against the actual repo (deps, existing convention, the other reviewer's
  verdict) and dismiss false positives with a WRITTEN rationale so teammates don't re-apply them.
- **A completed review can be against STALE (pre-fix) code** — ground-truth EVERY finding against the
  CURRENT on-disk file before acting. Tells: the finding labels (M2/M8/…) exactly match the fix-commit's
  own `FIX M8` annotations (the review described the pre-fix state), and files NOT in the modified set
  still disagree at the cited lines. Never re-open the loop or "re-fix" on a stale review — verify, then
  dispatch a FRESH round against the current tree (put `git diff origin/main` + "read the LIVE files" in
  the brief).
- **Check the upstream version before filing a codex-companion bug.** Update the local cache with
  `CLAUDE_CONFIG_DIR=/Users/moi/.claude-bm claude plugin update codex@openai-codex` and diff — the
  background-job-lifecycle surface has many open upstream issues, so a local-version bug is likely a dup
  or already fixed. (The 1.0.4→1.0.6 bump changed nothing in that surface; the fix belongs in your own
  driver, not a hoped-for upstream change.)

## /security-review & working-tree diff caveats
`/security-review` (built-in) and `--scope working-tree` read the WORKING TREE — run BEFORE committing;
they show unstaged + untracked changes, so you don't commit-then-review. Two empty-diff traps:
- **Fully-staged tree** (e.g. right after `git add` resolved a conflict): the bootstrap runs a plain
  `git diff` (unstaged-vs-index), so an all-staged tree yields an EMPTY "DIFF CONTENT". Capture
  `git diff --cached` yourself and hand it to a manual sub-agent driving the skill's 3-step process.
- **Inside a git worktree:** the skill computes git in the PRIMARY checkout and reports "working tree
  clean" — reviewing nothing. Simplest fix when your work is uncommitted: COMMIT first (a local un-pushed
  commit populates the full `origin/main..HEAD` branch diff; amend later if flagged). Otherwise generate
  the diff yourself (`git -C <worktree> add -A -N <path>; git diff <path> > d.diff`) and hand it to an
  independent agent briefed with the security objective.
