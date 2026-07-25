# Codex model × effort bake-off (direct-drive, blind-adjudicated)

Before committing a full translation/quality job to one `(model, effort)`, MEASURE on a
representative slice by driving several isolated codex arms directly via `codex-companion.mjs`
(bypassing any plugin), then blind-adjudicate. Cheap and decisive.

## Drive N arms
```
node "$CC" task --write --model <id> --effort <e> --cwd <armdir> --prompt-file <p> --background
```
- Runtime path: `~/.claude/plugins/cache/openai-codex/codex/<ver>/scripts/codex-companion.mjs`.
  Config `~/.codex/config.toml` (model default, `model_reasoning_effort`; `sandbox_mode=danger-full-access`
  → codex writes anywhere).
- `--effort` whitelist = `none/minimal/low/medium/high/xhigh` ONLY — config-level `ultra`/`max` are NOT
  flag-passable (config-default only). Model: any string passes (only `spark` is aliased).
- **Each arm needs its OWN `--cwd`** (or per-arm output filenames): the stock plugin prompt writes a
  FIXED `segments/<seg>.draft.json`, so one shared dir = only the last arm's output survives.

## Operational gotchas (each one bites)
- **zsh does NOT word-split unquoted vars.** `for x in "a b c"; set -- $x` leaves `$2/$3` EMPTY → arms
  launch with empty `--model`/`--effort` and silently fall back to the config default (all arms identical).
  Use `while read a b c`, a `launch(){…}` function called with literal args, or `set -o shwordsplit`.
  Verify by echoing the built command.
- **Jobs are tracked per workspace-root (resolved from cwd).** `status`/`result`/`cancel` only find a job
  when run with a MATCHING `--cwd`; a `cancel` from a different cwd → "No job found". Kill strays by pid
  from the on-disk job json:
  `~/.claude3/plugins/data/codex-openai-codex/state/<ws-hash>/jobs/<id>.json` → `.pid` → `kill`.
- **`status`/`result` do NOT record the effective model/effort.** The background job REQUEST json DOES
  retain the *requested* values → verify an override reached codex by reading the job json's `model`/`effort`,
  not `status`.
- **Wait via a `run_in_background` Bash poll-loop** over the job jsons' `status` (terminal =
  `completed`/`failed`/`cancelled`); the harness kills an over-long monitor, so cap the loop (≤ ~40 iters)
  and re-arm.

## Blind adjudication
Assemble the SOURCE + all arms' outputs shuffled as Version A/B/C/D (mapping saved to a PRIVATE file),
and feed them to a neutral codex judge (`--effort xhigh`) that does NOT know which is which; ask for
per-criterion wins + a ranking + the best + a concrete accuracy-error inventory. Use a DIFFERENT model as
judge than the front-runner (adversarial: a Terra judge still ranking Sol arms on top is a strong signal).

## Effort winners: an n=1 bake-off measures NOISE — this one did

A 2×2 (Sol vs Terra × high vs xhigh) bake-off on a real Hasidic-Hebrew→English slice reported
**`high` won**, and that hardened over restatements into "`xhigh` over-reaches → more hallucination".
**A properly designed replication in 2026-07-25 refuted both.** Same model (Sol), same book, but with
the three controls the original lacked — **3 replicates per cell**, a **blind cross-model judge**
(Terra grading Sol, one translation per job, arm hidden), and **counterbalanced presentation order** —
the two effort tiers came out **indistinguishable**:

- fidelity defects (additions + omissions + mistranslations): high 1.17 vs xhigh 0.50 per run, against
  a **within-arm replicate spread of 3.0** → inside noise;
- `addition_unsupported` — the "xhigh hallucinates" endpoint — 0.00 vs 0.33, i.e. **two events total
  across 12 runs, both in one xhigh run**, against a spread of 2.0 → no support in either direction;
- counterbalanced pairwise: 3–2 split with `narrow` margins, no position bias (slot A 5 / slot B 7),
  judge self-consistent on 5 of 6 pairs;
- the only robust difference was **latency: xhigh ≈2× slower**.

**The transferable rule: never conclude an effort/model winner from one run per cell.** Without
replicates you cannot separate an engine difference from run-to-run variance, and the runtime exposes
no seed or temperature, so replicates are the ONLY handle. Add: a judge from a different model family
(so the engine never grades itself), both presentation orders per pair (a fixed order lets slot
preference masquerade as quality), and a decision rule written down BEFORE results — "a difference
counts only if it exceeds the within-arm spread; a null defaults to the standing policy." Preserve
every arm output and verdict on disk: the original bake-off became unre-scorable precisely because
none of its translations survived. Worked example with all scripts:
`ssk-vol2-en-run/effort-experiment/` (`PROTOCOL.md` pre-registered, `RESULTS.md`).

What still stands from the original: **Sol above Terra** (both Sol arms outranked both Terra arms) —
but that half was never replicated either, so cite it as indicative, not settled.

**Testing whether a higher tier has better RECALL is a different experiment, and it has one trap.**
Re-run the identical prompt at the other tier on a few units, then diff its finds against the
**RAW output of the original tier** — never against the finished artifact. A canon (or any filtered
deliverable) has had scope exclusions applied, so diffing against it re-labels every deliberate
exclusion as a "miss" and manufactures a false positive result. Two further practicalities: swap the
literal `Effort:` prose opener as well as the `--effort` flag (the prose wins), and match entities
by normalized English AND mark-stripped Hebrew, or spelling variants of the same person show up as
fresh finds. Run on the SSK canon (3 of 40 segments): `xhigh` surfaced **no in-scope name `high`
missed** — its only three novel items were all scope-excluded categories — while capturing *fewer*
surface variants in 2 of 3 segments and dropping honorific-laden full forms that `high` kept. Since
surface variants are exactly what a surface-keyed dictionary needs, that also refutes the original
bake-off's "xhigh has better mention-recall" claim.

**Entity scope + homonym-split are PROMPT-DISCIPLINE, not model capability** — with explicit rules
(exclude God/Messiah; keep distinct namesakes separate; mark `[source]` vs `[external]`) ALL models complied
and split homonyms correctly. Smoother-but-freer models tend to introduce real mistranslations/reversals;
only the highest-effort arm reliably flagged `uncertain` (better epistemic caution) — but that is an
OBSERVATION from the original single-run bake-off, unreplicated like the Sol-above-Terra half above:
indicative, and not a validated tier selection. Takeaway: for faithful
text work, put the accuracy-critical constraints in the PROMPT — that is the lever with demonstrated
effect. No tier has been validated as a winner (the replication above found high and xhigh
indistinguishable on fidelity), so choose the tier from the standing policy and the measured latency
cost, never from a bake-off verdict.
