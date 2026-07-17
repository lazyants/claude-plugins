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

## Durable finding: more reasoning ≠ more accuracy
On a real Hasidic-Hebrew→English run (Sol vs Terra × high vs xhigh): **`high` won** (own eyeball + blind
judge). **`xhigh` does MORE** (re-vocalizes source, richer entity notes, better mention-recall) **but NOT
more accurately — it over-reaches: more elaboration → more hallucination surface** (e.g. an unverified
biographical "of Dashiv" epithet, God/Messiah pulled into a people index). `high` often equals or beats
`xhigh` for faithful translation and is cheaper.

**Entity scope + homonym-split are PROMPT-DISCIPLINE, not model capability** — with explicit rules
(exclude God/Messiah; keep distinct namesakes separate; mark `[source]` vs `[external]`) ALL models complied
and split homonyms correctly. Smoother-but-freer models tend to introduce real mistranslations/reversals;
only the highest-effort arm reliably flags `uncertain` (better epistemic caution). Takeaway: for faithful
text work, don't reflexively max out effort — pick the tier a blind bake-off actually validated, and put
the accuracy-critical constraints in the PROMPT.
