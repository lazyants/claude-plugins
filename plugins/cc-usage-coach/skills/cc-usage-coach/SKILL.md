---
name: cc-usage-coach
description: >-
  Analyze and reduce your own Claude Code token / usage-limit consumption. Use
  when the user asks where their Claude Code (Max/Pro) limit tokens go, why they
  hit usage limits, how to use fewer tokens, or wants a personalized report of
  their CC session history with ranked, behavior-aware ways to cut usage.
---

# cc-usage-coach

Produce a personalized, behavior-aware report of where the invoking user's
Claude Code **limit tokens** go, plus ranked proposals to use fewer. The
Python scripts **MEASURE**; **you (the runtime LLM) CONCLUDE**. Significance,
behavior detection, and advice ranking are your job — judged against the
user's OWN baselines in the pack (`p50`/`p90`), never against absolute numbers.

The scripts live next to this file in `scripts/`. Invoke them by a path
derived from this skill's directory so the current working directory does not
matter (e.g. `python3 "<skill-dir>/scripts/extract.py"`).

## Steps

1. **Extract.** Run `scripts/extract.py`. This reads the local Claude Code
   session logs into `dataset/` (~1 min). If a recent `dataset/` already
   exists you may reuse it and skip this step.

   By default only the standard Claude Code config dir is scanned. If the user
   runs more than one config dir (multiple identities / `CLAUDE_CONFIG_DIR`
   setups), set `CC_COACH_CONFIG_DIRS` to a comma-separated list of the EXTRA
   dirs to scan as well, e.g. `CC_COACH_CONFIG_DIRS=dir-a,dir-b` (use the user's
   real dir names; the placeholders here are generic on purpose).

2. **Build signals.** Run `scripts/signals.py`. It writes three files:
   - `signal_pack.json` — compact, path-free AND project-name-free, **shareable**.
     Project labels in it are OPAQUE IDs (e.g. `proj_a1b2c3d4e5`), not real names.
   - `source_index.json` — `{source_ref -> absolute path}`, **LOCAL-ONLY**
     (`0600`, gitignored). Used only to locate sessions on disk.
   - `project_index.json` — `{opaque project id -> real project name}`,
     **LOCAL-ONLY** (`0600`, gitignored). Used only to show real project names
     in YOUR report.

   Both extract and signals write under the plugin's own directory by default.
   When you invoke the scripts from outside the plugin dir, or the plugin dir is
   read-only, set `CC_COACH_OUT` to a writable output dir and the scripts will
   put `dataset/`, `signal_pack.json`, and `source_index.json` there instead.

3. **Read the pack (resolve names locally).** Read `signal_pack.json`; never read
   `dataset/sessions.jsonl` or the per-turn files into context — the pack is the
   deliberate, path-free summary. The pack's project labels
   (`pareto.top_projects[].p`, `candidate_sessions[].p`) are OPAQUE IDs; to name a
   project in YOUR report, look its ID up in the LOCAL-ONLY `project_index.json`
   (`{id -> real name}`). Show the user the real name; NEVER put a real project
   name in anything meant to be shared.

4. **Inspect the top candidates.** Pick the top 3–5 entries in
   `candidate_sessions.items` (by `cost_pct` / `anomaly_rank` / `why`). For each,
   run `python3 "<skill-dir>/scripts/arc.py" <source_ref>` to get a compact,
   path-free **arc digest** of that session — the sequence of user prompts and
   the edit/agent activity over time. **Judge from the arc, not the opener.** A
   long session whose prompts trace one coherent task — even across compactions,
   or an autonomous loop that keeps working the same goal — is *warranted*; call
   it wasteful only when the prompt arc shows unrelated task-switching (a
   throwaway opener that became a catch-all bench, several disjoint jobs sharing
   one session). This human-judgment pass is the point of the skill — the pack
   flags candidates, you decide.

   Two constraints on what the arc can prove:
   - You see the **MAIN transcript only** — file changes a subagent made through
     its own runtime are invisible here, so "nothing changed between X and Y" can
     never be proven from this view alone.
   - The arc digest is **LOCAL-ONLY** (it contains the user's prompt text).
     Never quote it verbatim in the shareable report — summarize the shape of
     the work ("one refactor across ~40 turns"), never the prompts themselves.

5. **Write the report**, in exactly these five sections:
   - **Where tokens go** — the `split` (creation/output/input %), `pareto`
     concentration (projects/sessions carrying most quota), recache share.
   - **Session length & shape** — the real-session turn-count distribution and
     what it implies about task size (details below).
   - **Ranked levers (impact × ease)** — concrete changes ordered by your
     own estimate of limit-token savings vs effort. Separate LIMIT levers
     (what actually moves the Max/Pro limit) from $-billing-only reference.
   - **Behavior observations** — what the candidate-session inspection showed:
     warranted vs wasteful patterns, with the evidence you saw.
   - **Caveats** — every low-confidence slice, every directional-only proxy,
     and the currency uncertainties below.

### The "Session length & shape" section

Read the `session_length` pack block together with `baselines.turns`, and
present the real-session turn-count distribution as a table. Describe the counts
ACCURATELY — these are different kinds of JSONL file, not "the user's sessions"
vs "side-thread":

- `session_length.by_dir_class.real` — the top-level sessions the user actually
  drives.
- `session_length.by_dir_class.subagents` / `by_dir_class.workflow` — SEPARATE
  side-thread JSONL files **spawned by** those top-level sessions (subagent and
  workflow threads). They are not the user's own sessions; do not lump them in.
- `session_length.real_with_side_turns` — how many of the top-level sessions
  fanned out inline (spawned side threads).

Do **not** label the dir-class split as "real vs side-thread." It is top-level
vs the kinds of side-thread file.

The block also carries `real_turns` `{p50, p90, p99, max, mean, histogram}` and
`real_dur_min` `{p50, p90, max, note}`. Render the turn-count percentiles and
histogram as a table. State the wall-clock caveat from `real_dur_min.note`
verbatim: wall-clock overstates active work because sessions resume across days.

Frame session length as task **SIZE**, not sprawl. This user starts a fresh
session per task, so a long session is usually a big task done in one place —
warranted, not waste. Only the arc inspection in step 4 can tell a genuinely
sprawling session from a large coherent one.

## Core principle

The Python computes metrics, per-user baselines, and ranked candidates with
**no verdicts and no magnitude thresholds**. You supply every conclusion. A raw
number means nothing on its own — compare it to the matching `baselines.*.p50`
/ `.p90` for *this* user. The same turn count that is unremarkable for a heavy
user is an outlier for a light one; the pack encodes that, you must honor it.

## Verification gate (before you rank ANY lever)

This is the *conclude* half of "Python measures, you conclude," and it is where
this skill most easily goes wrong. Every lever you rank MUST rest on evidence
that *measures the behavior you are claiming* — a session you actually opened in
step 4, or a pack metric that isolates it (e.g. `fan_out.trivial_subagent_rate`).

- A lever drawn from the user's `CLAUDE.md` / workflow / config rather than from
  the data is a **hypothesis** — label it "(unverified)" and do NOT rank it.
  State it separately at most.
- Distrust any proxy that cannot separate waste from legitimate work. Before
  citing one, name what *else* could produce the same number; if a benign cause
  fits, call it directional and rank it low or not at all. (Seen in practice: a
  reviewer re-running with "no file edit since its last run" looks like a wasted
  clean-tree loop — but a subagent may have applied the fix through its own
  runtime, which the main transcript never shows. The proxy cannot tell the two
  apart, so it is an upper bound, not a finding.)
- Confident-but-unverified is the single failure that destroys this report's
  trust. When you cannot separate waste from warranted, downgrade and say why.

## Output style

Write for a reader who does not live in token jargon.

- **Plain English, short sentences.** Explain any token term the first time it
  appears — e.g. "cache creation = building the context the model re-reads each
  turn," "cache read = re-reading that context (free for usage limits)."
- **Use a TABLE when you present 3+ comparable rows or a numeric breakdown.**
  That covers the where-tokens-go split, the ranked levers, the
  candidate-session inspection, and the session-length distribution. A table
  makes the comparison legible at a glance.
- **Use prose for caveats, nuance, and single facts.** Situational — do NOT
  force a table around a single number or a one-line conclusion.

## Hard rules for this report

Honor `currency_notes` in the pack **verbatim**:

- `reads_excluded_from_limits: true` — **cache reads are FREE for usage
  limits.** Never count reads toward the limit, never present a big read figure
  as a problem to fix for limits. (Reads still cost money on pay-as-you-go; keep
  that in the $-billing section only.)
- `read_exclusion_exceptions: ["claude-haiku-3.5"]` and
  `haiku_4_5_reads_uncertain: true` — the read exclusion is documented for all
  models *except* `claude-haiku-3.5`; whether **Haiku 4.5** reads are truly
  excluded is **uncertain**. Flag this explicitly as unverified; do not assert
  Haiku-4.5 reads are free, and do not silently count them either.
- `5m_write_means_overage: false` — a 5-minute cache write is **NOT overage**.
  Side-thread / subagent writes are 5m by design, so a 5m write carries no
  billing-tier signal. Never call a 5m write "overage."
- `output_limit_weight: "undocumented"` — report output in **raw tokens / %
  of quota**. Do **NOT** claim output is weighted ~5x toward the limit; the 5x
  figure is a $-billing ratio, not a limit weight.

Also:

- Treat `recache_excess_proxy` (and `recache_share`, `cr_peak_mult`) as a
  **rough directional signal, not a bound** — it estimates discretionary
  re-caching over a build-floor, not a hard savings number.
- **External reviewers run on a separate budget.** codex and other non-Claude
  reviewers do their heavy compute outside the Claude usage limit; only the
  Claude-side spin-up and reading their findings back counts. Never credit (or
  blame) their full review cost as limit tokens.
- **Discount any slice with `confidence: "low"`** (small `n`, or
  `ratio_vs_trailing_median: null`). Mention it as weak evidence at most.
- **Separate LIMIT levers from $-billing reference.** The user is the subject;
  state which tier matters for them. Limit savings ≠ dollar savings.
- If `corpus.insufficient_data` is true (or `n` is small on the baselines),
  **hedge hard**: present findings as tentative, do not rank aggressive levers.
- **Never quote a local filesystem path or a real project name in a shareable
  output.** `source_index.json`, `project_index.json`, `dataset/sessions.jsonl`,
  and the arc digest carry real usernames / paths / project names — they are
  LOCAL-ONLY: do not archive, upload, paste, or quote them. In the pack, sessions
  are opaque `source_ref` and projects are opaque IDs; resolve them to real names
  (via `source_index.json` / `project_index.json`) ONLY in the user's local
  report. Only the path-free, project-name-free `signal_pack.json` is safe to
  share.

## Self-check before delivering

Re-read your finished report and **strip / repair**:

- any sentence implying reads count toward the limit;
- any sentence calling a 5m write "overage";
- any claim that output is ~5x-weighted toward the limit;
- any multi-row numeric comparison that is NOT rendered as a table — convert it;
- any token term used without a plain-English explanation on first appearance —
  add one;
- any verbatim arc-prompt text or filesystem path in the shareable report (e.g.
  anything containing `/Users/` or a real project/prompt string) — summarize
  instead;
- any ranked lever you did not actually observe in an inspected session or
  isolate with a pack metric — demote it to "(unverified hypothesis)" or cut it.

Only deliver once the report survives this pass.
