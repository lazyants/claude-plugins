# Driving translation dispatch by hand

Steps 0–W3a of the plugin are used as-shipped; only the translate dispatch (W5) is replaced by hand. Use this when driving segments yourself instead of the `mass-translate-wf` Workflow, or when the Workflow path does not converge.

## Prove W5 dispatch actually executes — a throwaway smoke test first

Before committing a full book's labor, build a tiny throwaway `durable_root` and drive it end-to-end once, to prove the W5 dispatch mechanics actually execute and converge (the plugin's W5 driver has previously failed to converge for reasons invisible until it is actually run — its white-box tests use a fake node stub, not a live run).

- **Use 2+ segments with real margin, never 1.** `extract.py.template` calls `start_segment` on EVERY `<h2>` (`extract.py.template:845-846`), so h2-count == segment-count. On a 1-segment book, W4 (review) eats the only segment and **W5 (translate) never executes** — the smoke test goes green without testing anything. Size the chapters so the smallest is comfortably (≥28%) short of wherever the review/translate split lands, so which segment ends up in W4 vs W5 cannot flip and invalidate the design.
- **Never let scaffolding live inside the artifact under test.** Test scaffolds (an agent's self-test copies, your own verification-run copies) landing inside the same `durable_root` the smoke test measures silently contaminate it. Move them out to a sibling directory the moment you notice them there, before trusting any measurement of the scaffold.

## Do NOT route the translate through `codex:codex-rescue`

The Workflow's `agent(translatePrompt, {agentType:"codex:codex-rescue"})` expects a BLOCKING call that yields a draft, but `codex:codex-rescue` **backgrounds** codex and returns a "waiting on background task" STUB. `draft_ready.py`'s ≤15-min poll then never sees a draft: every segment ends `translate-timeout`, no `segments/<seg>.draft.json` is written, and the ledger stays `in_progress`.

Dispatch each segment with a **blocking** `codex-companion.mjs task --write --wait` instead (config model Sol at high). Feed the plugin's own `segpack_<seg>.json` `blocks[].plain_text` as the source text, plus the translate/entities prompt. To run past the harness's background-window kill, launch it through the nohup detached driver (see the codex-runtime driving notes). Both translate AND review write into `durable_root`, so BOTH go through `task --write` — the review path is not read-only here.

## durable_root must be inside codex's writable workspace

Codex anchors its write sandbox to the launch cwd's workspace root. Dispatching via `Agent(codex:codex-rescue)` from the session repo while `durable_root` sits in a SIBLING dir OUTSIDE that repo → every codex write is rejected (`writing outside of the project; rejected by user approval settings`) → the draft never lands and `draft_ready` polls forever.

Fix: launch `codex-companion.mjs task --write` with **cwd = durable_root**. `task --write` sets `sandbox:"workspace-write"` anchored to `resolveWorkspaceRoot(cwd)`; the `Agent(codex:codex-rescue)` task path anchors to the session repo instead, and its review path is read-only. Prove it with a 2-byte write-sanity test before the real translate. This `task --write` route is also a MORE faithful "codex translator/reviewer" than the rescue-flavored Agent — the real Workflow uses generic `agent()` calls, not the adversarial-rescue one.

## Transcribe the Workflow's builders VERBATIM

Reproducing the per-segment loop by hand, the dispatch prompts must be the Workflow's OWN builders — `translatePrompt` / `reviewDispatchPrompt` / `matchedVerdict` in `mass-translate-wf.template.js` — transcribed verbatim, substituting only the template vars to literals: `RUN_ID`, `SEG`, `ROOT`, `PY`, `SOURCE_LANG`, `TARGET_LANG`, `VERSE_POLICY_INSTRUCTION_BLOCK`.

Every guardrail is baked into those builders and comes free with verbatim transcription — re-authoring the prompts drops them (this cost 3 successive rounds of codex plan-review to rediscover):
- RUN_ID minting + colon-free validation
- the dual-token `DRAFT_TOKEN_MISMATCH` stop
- the fabricated-loc authenticity gate `AUTHENTIC_LOC_RE=/^[^\s:]+:.+$/`

General principle: reproducing ANY automated orchestrator by hand → transcribe its builders and substitute the variables; every guardrail you re-derive is one you will miss.

The only deliberate deviations from the builders:
- they hardcode `Effort: high.` in the dispatch prompt → override to `xhigh.` per the translation-effort rule. (This prompt-level effort is a DIFFERENT knob from the profile's const `engine.effort`, which stays `high`. Note the actual SSK run dispatched at Sol@high. No tier is validated as a winner — for what the model×effort evidence does and does not support, see `skill:codex-runtime-driving` → `references/model-effort-bakeoff.md`, the single home for that question; the standing rule for translation dispatch is xhigh.)
- substitute `PY` → the venv python.

## The bypass generalizes beyond W5 — glossary/W3a batch dispatch too

The `agent(..., {agentType:'codex:codex-rescue'})`-anchors-writes-to-the-session-repo trap and the `task --write --cwd <durable_root>` bypass are not specific to translate/W5 — the SAME fix applies to the glossary Workflow's batch dispatch (W3a), which has the same sandbox problem when its `durable_root` sits in a sibling dir outside the session repo.

- The glossary phase's own poll mechanism is decoupled rather than blocking: each batch writes an `out_{i}.json`, polled via `canon_validate.py --check-batch`. So dispatch each batch with `codex-companion.mjs task --write --background --cwd <durable_root> --json "<prompt>"` — **`--background`, not `--wait`** (the batch script polls the output file itself instead of blocking on the call). `--cwd` is a real flag, resolved via `resolve_codex_companion.py`.
- Instead of hand-transcribing a builder's prose into literal prompt text, get a byte-exact prompt by literally EVAL-ing the template's own builder function (e.g. the glossary template's `batchDispatchPrompt`) and substituting the template variables — never re-author a builder, and evaling it is a stronger guarantee of verbatim-ness than retyping it by hand.
- Prove the write path first with a 2-byte sanity write before dispatching the real batch.
- Once the poll confirms completion, run merge/verify/audit via the plugin's own deterministic scripts directly (e.g. `canon_validate.py --merge-batches`) — these steps are code, not codex, and don't need the bypass at all.
