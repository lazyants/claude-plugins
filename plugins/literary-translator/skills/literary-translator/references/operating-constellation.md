# Operating constellation — review orchestration

This file is operator guidance, not a plugin mechanism — nothing here is
enforced by any script, gated by any schema, or read by any workflow
template. It answers a different question than `references/engine-loop.md`
and `references/orchestration-and-batching.md`: those two own *what the
roles are* and *how dispatch works* (hard rules, never profile-configurable).
This file is a recommended answer to "which concrete agent/model/effort
should sit in each seat, and how tightly should the review loop run,
today." It changes nothing about R1 role separation or the `pipeline()`-only
dispatch rule — the per-segment translate → gate → review → fix loop stays
exactly as specified there, always through a schema-validated workflow-level
`agent()` call, never ad hoc named-teammate fan-out. Pipeline role
assignment itself (who translates / reviews / fixes / orchestrates) is an
intake-time agreement, not a profile knob — see SKILL.md's "Intake &
proportionality" step.

Two parts, on purpose: a **durable pattern** that should stay true regardless
of which models or tool defaults exist this month, and a **dated snapshot**
naming today's actual defaults and models. Re-verify the snapshot before
trusting it — the pattern is the part meant to outlive any one generation.

## Durable pattern

- **Independent reviewer, always.** Whatever checks a piece of work must run
  as a *separate* process from whatever produced it — never let the
  producing context grade its own output; it rubber-stamps. Prefer
  **cross-engine** review (e.g. Claude translates, Codex reviews) — the
  strongest combination for catching errors — but at minimum use a fresh,
  separate agent. Require at least one independent reviewer engine, and make
  the choice configurable so a user with only Claude *or* only Codex still
  works: degrade gracefully to single-engine review rather than refusing to
  run.
- **Unit of review = the smallest independently-checkable deliverable**, not
  a vague "chunk": a translation's unit is one segment/novella (exactly
  where this plugin's own engine loop already dispatches — R1,
  `references/engine-loop.md`); a code change's unit is one script/file; a
  plan's unit is the whole plan.
- **Loop, with a cap.** Review → fix → re-check, repeated until the reviewer
  returns clean — only cosmetic findings or none at all, not merely "no
  gross ones." But cap the loop: stop after N rounds, or once only
  low-severity findings remain. Chasing cosmetic findings forever is exactly
  where cost blows up. (This plugin's own `engine.max_fix_rounds` is this
  exact cap, already wired into the per-segment loop — R1/R2,
  `references/engine-loop.md`, `references/false-green-gate.md`.)
  - **Fresh vs. resume, per round.** A *fresh* session gives an independent,
    unbiased look — the safe default, especially for the first pass hunting
    for anything wrong. A *resume* (same thread) is cheaper and more precise
    for a "confirm my fix holds" delta pass, because the reviewer already
    remembers exactly what it flagged. Always-fresh re-reads everything
    every round, which costs more tokens — worth it for the hunting rounds,
    wasteful for a pure confirm-the-fix round.
- **Pin effort explicitly — never inherit whatever the tool's config default
  happens to be that day.** Defaults drift (see the dated snapshot below);
  an unpinned review silently rides that drift.
- **Pick model tier by ROLE, not by name.** Model names and generations go
  stale fast. Route a cheap/fast model to routine per-item checks, mechanical
  validation, and style passes; route the strongest available model to the
  hardest adversarial-correctness review and to planning. Use a deliberate
  split — never a blanket "always the cheap one," and never a blanket
  "always the most expensive one" either.

## Dated snapshot (as of 2026-07 — re-verify before trusting)

- **Effort ladder (Codex):** `none / minimal / low / medium / high / xhigh`.
  Codex's own config default has drifted to `xhigh` — do not rely on it
  silently; pin explicitly per the durable-pattern rule above. `high` is the
  cost-sane default for routine review; reserve `xhigh` for the genuinely
  hardest correctness passes and for plan review. Do **not** use Claude's
  `max` effort tier for this work — it is overkill.
- **Model tier by role:** strongest = **Opus 4.8** (hardest
  adversarial-correctness review, planning); cheaper-but-strong = **Sonnet
  5** (several times cheaper, good enough for most checks, but *not*
  identical to Opus on subtle or correctness-critical ones) — use a split,
  never a blanket substitution either direction.
- **Example constellation** for running this plugin's own work: orchestrator
  = Claude Code at its highest-effort mode (as of 2026-07, "ultracode" at
  `xhigh` reasoning effort), doing the parallel decomposition the durable
  pattern describes; implementer (per-segment translate/fix) = Sonnet 5 at
  `effort: "high"` (this plugin already hard-locks `engine.effort: "high"`
  for every codex accuracy-bearing call); adversarial reviewer =
  Codex/GPT-5.5 via `codex:codex-rescue`, pinned explicitly to `high` for
  routine review or `xhigh` for the hardest correctness passes — the same
  role R7 already requires to be schema-validated
  (`references/workflow-schema-validation.md`).

Model names, generations, and tool config defaults all change fast — a name
or default pinned here today is stale by the time this project reruns next
quarter. Before adopting this snapshot, check for and prefer whatever the
current latest capable models and defaults actually are; the durable pattern
above is what's meant to survive any one generation.

## Cross-references

- `references/engine-loop.md` — R1 role separation, the hard-locked
  `engine.effort: "high"` requirement, `max_fix_rounds` as the loop's cap.
- `references/orchestration-and-batching.md` — `pipeline()` dispatch
  mechanics, the smallest-fan-out principle the "unit of review" rule
  mirrors.
- SKILL.md's "Intake & proportionality" step — where pipeline role
  assignment (who translates/reviews/fixes/orchestrates) is actually agreed
  and recorded, per project.
