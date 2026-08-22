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

## Re-reviewing an already-translated segment — no shipped review-only path

Re-reviewing a book already translated (e.g. after a hand-correction) is a routine operator need
with no shipped path. `mass-translate-wf.template.js` ends in `pipeline(SEGS, translateStage,
reviewFixLoop)`, and `translateStage` (cite it by name; it moves) is UNCONDITIONAL — it does not consult
`derive_next_action()`, the ledger, or whether a draft already exists; it dispatches
`codex_job.py --kind translate` and returns. Feeding it segments whose drafts were hand-corrected
**overwrites that work**. `glossary-pass` and `skeptic-pass` contain no review stage at all.

**The sanctioned substitute is the template's own builders, transcribed verbatim (never
re-authored) — per the rule above:**
- `reviewDispatchPrompt(seg, roundLabel)` — the ENTIRE function body, from its `function` line to
  its closing brace, including the trailing `⟦JOB_OUT⟧` write-destination line and the final
  `Return exactly the line: REVIEWED` line. Do not cite or cut by line number: the ranges shift, and
  a range that stops early yields a prompt with no write destination and no return line.
- `reviewDrivePrompt(seg, roundLabel)` — likewise the entire function body, the exact
  `codex_job.py --kind review` launch.
- Acceptance gate: `review_ready.py --expect-token`.

`codex_job.py` supplies the isolated `JOB_OUT` and atomically promotes it to the canonical
`<seg>.review.json`, so no part of the artifact contract is re-implemented. Sturdier than hand
transcription: cut the builder out by brace balance and assert the slice still contains its
contract lines (`validate_draft.py`, `draft_sha1.py`, `DRAFT_TOKEN_MISMATCH`, `canon_map`,
`⟦JOB_OUT⟧`, `REVIEWED`) — a template shift then fails loudly instead of rendering a weakened
prompt.

### Three traps, the first silent

1. **A fresh `RUN_ID` yields ZERO verdicts and no error.** `mass-translate-wf.template.js`'s `reviewDispatchPrompt()` — its `DRAFT_TOKEN_MISMATCH`
   instruction — makes the
   reviewer compare the DRAFT's own `dispatch_token` against the literal `RUN_ID + ":" + seg`; on
   mismatch it returns `DRAFT_TOKEN_MISMATCH` and **writes nothing at all**. Read `RUN_ID` out of
   the drafts themselves, per segment (do not assume one per book), and take `roundLabel` as the
   successor of the label in the existing `<seg>.review.json` — never reuse it.
2. **Back up before the first dispatch.** Promotion is `os.replace()` with no backup and no
   post-confirm (`codex_job.py:48`), so every existing `<seg>.review.json` is destroyed on first
   write. Copy them out first — but do NOT delete the originals, since the round label is read
   from them and `derive_next_action()` consults them.
3. **`--effort` here is the PROJECT's, not a tier I am choosing.** The template threads
   `engine.effort` from `profile.yml` (both live books: `xhigh`). Dropping the flag puts a weaker
   reviewer on the round that confirms a stronger one's. No conflict with the standing "never pass
   a tier override" rule, which governs tiers *I* assign to codex-rescue.

### `draft_sha1.py` cannot see formatting — use it to tell content from noise

It parses the draft, drops `dispatch_token`, re-serializes with sorted keys and compact
separators, and hashes THAT. So a mismatch against `reviewed_draft_sha1` is a **content** change,
always; re-serialization, key reordering and a missing trailing newline leave it fixed. Two
independent records carry the hash — the ledger entry and the `*.review.json` artifact — so they
cross-check.

**mtime does not.** A later campaign, or a `cp -f` restore, overwrites the mark of an earlier one,
so mtime clustering measures the LAST write and never the volume of edits.

## The bypass generalizes beyond W5 — glossary/W3a batch dispatch too

The `agent(..., {agentType:'codex:codex-rescue'})`-anchors-writes-to-the-session-repo trap and the `task --write --cwd <durable_root>` bypass are not specific to translate/W5 — the SAME fix applies to the glossary Workflow's batch dispatch (W3a), which has the same sandbox problem when its `durable_root` sits in a sibling dir outside the session repo.

- The glossary phase's own poll mechanism is decoupled rather than blocking: each batch writes an `out_{i}.json`, polled via `canon_validate.py --check-batch`. So dispatch each batch with `codex-companion.mjs task --write --background --cwd <durable_root> --json "<prompt>"` — **`--background`, not `--wait`** (the batch script polls the output file itself instead of blocking on the call). `--cwd` is a real flag, resolved via `resolve_codex_companion.py`.
- Instead of hand-transcribing a builder's prose into literal prompt text, get a byte-exact prompt by literally EVAL-ing the template's own builder function (e.g. the glossary template's `batchDispatchPrompt`) and substituting the template variables — never re-author a builder, and evaling it is a stronger guarantee of verbatim-ness than retyping it by hand.
- Prove the write path first with a 2-byte sanity write before dispatching the real batch.
- Once the poll confirms completion, run merge/verify/audit via the plugin's own deterministic scripts directly (e.g. `canon_validate.py --merge-batches`) — these steps are code, not codex, and don't need the bypass at all.
