# Driving translation dispatch by hand

Steps 0–W3a of the plugin are used as-shipped; only the translate dispatch (W5) is replaced by hand. Use this when driving segments yourself instead of the `mass-translate-wf` Workflow, or when the Workflow path does not converge.

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
- they hardcode `Effort: high.` in the dispatch prompt → override to `xhigh.` per the translation-effort rule. (This prompt-level effort is a DIFFERENT knob from the profile's const `engine.effort`, which stays `high`. Note the actual SSK run dispatched at Sol@high, and a model×effort bake-off found the config model at high a strong performer — xhigh can over-reach — so weigh the bake-off, but the standing rule for translation dispatch is xhigh.)
- substitute `PY` → the venv python.
