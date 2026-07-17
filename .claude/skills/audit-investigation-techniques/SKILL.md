---
name: audit-investigation-techniques
description: Techniques for multi-agent audit, gap-hunting, and research work — use when auditing another Claude Code session's behavior from its on-disk JSONL transcript, running a multi-agent sweep to file non-duplicate GitHub issues for repo/plugin gaps, or briefing Explore/research agents during planning (especially right after a merge, when the local tree lags origin/main and agents confidently report shipped code "doesn't exist").
---

# Multi-agent audit & investigation techniques

Three recipes that share one spine: you fan work out to **fresh-context agents**, so every
prompt must carry a ground-truth brief, and every finding they return must be re-verified
against **current** source before you act on it. The recurring failure is trusting an agent's
map or verdict without that re-check — a wrong path, a stale tree, or your own fed-in premise
routinely turns out wrong.

Shared discipline across all three:
- **Embed a ground-truth brief in every agent prompt.** Fresh context = they know nothing you know.
- **Re-verify every finding against current source yourself.** Do not fold an agent's claim into a plan, an issue, or a report on its word alone.
- **A wrong path or "X doesn't exist" is NOT a refutation.** Locate the file / read the real ref before dismissing a finding.

---

## 1. Auditing another Claude Code session from its transcript

**Locate.** Every session writes JSONL to `~/.claude/projects/<cwd-slug>/<session-id>.jsonl`;
that session's forks live under `~/.claude/projects/<cwd-slug>/<session-id>/subagents/agent-*.jsonl`.
`<cwd-slug>` = the session's cwd with every `/` replaced by `-`. `projects/` is symlinked into
one physical `~/.claude/projects` from every config-dir profile, so ANY session's transcript is
findable regardless of which profile launched it:

```
find ~/.claude/projects -name '<id>*.jsonl'
```

**Parse OUT of context — never read the raw JSONL in.** It can be MB+ and is LIVE (still being
appended). Write a deterministic parser that emits a compact digest (user msgs + assistant text +
`thinking` blocks + tool_use INPUTS + truncated tool_results) and prints only stats. Parser gotchas:
- Message `content` is a **str OR a list** — handle both.
- Block `type` ∈ `{text, thinking, tool_use, tool_result}`; record `type` ∈ `{user, assistant, system}`.
- A fork transcript's **FIRST `Agent`/`fork` record (idx 0/1) is the PARENT-dispatch seed, NOT a
  nested spawn** — do not miscount it as the fork dispatching its own subagent.

**Run a multi-lens adversarial Workflow over the DIGEST** (not the raw). N critical lenses —
workflow-fidelity, decision-autonomy, data-integrity/fs-risk, subagent-trust, plugin-correctness,
domain-correctness — then a per-finding adversarial verify (verdicts `CONFIRMED` / `PLAUSIBLE` /
`REFUTED`) → synthesize. Because the subagents have fresh context:
- Embed the ground-truth brief (everything you already know) in **every** lens and verify prompt.
- Instruct **STRICT READ-ONLY** — the audited session is LIVE; never write to its files.

**The verify pass earns its cost — let it correct you.** It has repeatedly refuted or tempered
findings, *including premises you fed in yourself as ground truth*. Do not treat your own inputs
as settled and do not relay your inline pre-checks to the user as "confirmed" before the
adversarial pass runs — they are hypotheses until then.

**Version skew (the codebase moved forward since the audited run).** When the run happened on an
old version and the plugin has since shipped fixes, the dominant per-finding question flips from
"is it a real bug" to "is it STILL SHIPPED or already fixed by an intervening PR." So:
- Embed the full intervening-issue list in the brief and make the verify **schema** classify
  `still_shipped` vs `fixed-by-#n`.
- Judge EVERY finding against **current** source, never the version the run (or a gap memory) describes — a restructure can moot a whole class at once.
- If the audited session wrote its own curated defect list ("gap memory"), use it as the SEED but
  re-verify each item — its "this is a bug" may already be fixed or be context-dependent. Expect a
  high dedup/refute rate.

**Deliverables.** Rank findings by severity × confidence. Separate **plugin bugs** (→ GitHub
issues) from **session-behavior findings** (→ tell the user, not the tracker). Own your own
corrected premises explicitly.

---

## 2. Multi-agent gap sweep → non-duplicate GitHub issues

For "investigate whether the plugins have gaps / ideas — file issues, but no duplicates of
existing ones."

**Briefing (this is what does the deduping):**
- **One investigator per plugin + one repo-wide**, all READ-ONLY — explicitly forbid file edits
  and git state ops (they share the cwd).
- **Paste the FULL open AND CLOSED issue titles for that agent's scope into its prompt.** The
  closed list matters as much as the open one — on a plugin with many closed issues, most "obvious"
  findings are already fixed, and an agent without that list re-files them. Get both cheaply:
  ```
  gh issue list --state open|closed --limit 300 --json number,title --template …
  ```
- **Demand a `dedup:` line per candidate** naming the closest existing issue + why this one is
  distinct. This is what makes them self-police; they then also volunteer their *rejected*
  candidates ("real, but that's exactly #34 — not re-filed"), which is how you see the negative space.
- **Cap candidates (~6) and say "nothing beyond existing issues" is a valid result.** Quality bar, not a quota.
- **Warn about side-effect traps in the thing under test.** If an agent must EXECUTE the code under
  test to prove a finding (e.g. running a hook to prove a bypass), name the hazard in the brief and
  make it run under an isolated `HOME` so it can't append fake rows to the user's real audit log.

**Lead's job before filing (do NOT skip):**
- **Spot-verify every candidate's file:line against current source yourself.**
- **A WRONG PATH IS NOT A REFUTATION — locate the file before dismissing the finding.** An agent
  can cite a plausible-but-wrong directory; `find -name <file>` finds the real one and the line
  numbers are often exactly right. Path drift ≠ bad finding (distinct from a genuinely fabricated
  report, whose tell is work that never lands, not a mis-typed dir).
- **Merge cross-agent duplicates.** The same cross-cutting defect surfaces independently from N
  agents → ONE issue, not N. Only the lead can see this; each agent thinks it's a fresh find.
- **Not every finding is a new issue.** A near-dupe with new information is a **comment** on the
  existing issue (a scope-extension, or "your instance is already fixed, close in favor of #n"), not a new number.
- **A finding can be MOOT by another change in the same session** — re-check late findings against the tree as it now is.

**Filing mechanics.** Bulk-file from a script, never N ad-hoc `gh` calls. Write a Python list of
`(title, [labels], body)` tuples + a `--dry` flag that prints title + labels only. Run the dry
pass, eyeball it, then file — issues are outward-facing and tedious to unfile, so the dry pass is
the last cheap checkpoint. Give every body the same shape — `### What` / `### Impact` / `### Fix` /
`### Dedup` — and a footer noting it came from an automated investigation and should be
sanity-checked. Label every issue with all three: `plugin:<name>` + a type + a severity.

---

## 3. Briefing research/Explore agents against a stale local tree

**The trap.** When publishing = merge-to-main and the user works across sessions, local `main`
routinely lags `origin/main` — and the missing commit is often the *exact* prior phase you are now
extending (you plan phase N+1 right after phase N merged). Research/Explore agents read the
**working tree**, so they read pre-N code and produce confidently-wrong maps: "there is no X
extractor" for a symbol that shipped in the version you're building on. A wrong "X doesn't exist"
map is worse than no map — it silently steers the plan toward re-inventing shipped code.

**The tell:** a research agent reports a symbol/file/feature "doesn't exist" or "there is no X" for
something you KNOW shipped in the version you're building on → it read the stale tree, not the real baseline.

**How to apply (at research kickoff, before dispatching any agent):**
- `git fetch`, then check `git status` for "behind by N" and `git log --oneline origin/main -3`. If local lags, that's the trap.
- **In plan mode (can't fast-forward):** brief EVERY research/Explore agent EXPLICITLY to read the
  baseline via `git show origin/main:<path>` (and grep via `git grep <pat> origin/main -- <path>`),
  and to STATE which ref they read. Make it an instruction, not a hope — most agents won't figure
  out the tree is stale on their own.
- **When you CAN mutate:** fast-forward first (`git merge --ff-only origin/main` on a clean tree) so the tree matches, then research normally.
- The BUILD phase sidesteps this by cutting the worktree from origin/main (`git worktree add … origin/main`), but PLAN-phase research runs before that worktree exists — so it needs the explicit-ref briefing.
- **Cross-check any agent's "doesn't exist" / "not wired" claim against origin/main yourself** before folding it into the plan.
