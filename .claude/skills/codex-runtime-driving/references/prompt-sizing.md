# Sizing a prompt against the context window (non-Latin script / JSON payloads)

Fires when sizing a prompt — whole-corpus review, consolidation, map-reduce chunking — whose payload
is non-Latin (vocalized/pointed Hebrew, Arabic with harakat, Devanagari, heavy CJK) or is mostly JSON.
Two independent traps, both silent until a job dies.

## Trap 1 — `chars/4` is a Latin-prose ratio, not a universal one

Pointed Hebrew carries a combining niqqud mark per letter; those marks tokenize separately, so the
real chars-per-token is far below 4. Measured on SSK vol.2 (`gpt-5.6-sol`, nominal 272K window —
but see Trap 3: the number you may actually spend is smaller):

| prompt | chars | my `chars/4` estimate | reality |
|---|---|---|---|
| whole-corpus review (461K chars Hebrew + canon) | 611K | "~153K tokens — fits" | **overflowed** the 272K window |
| chunked review (115K chars Hebrew + 148K chars JSON canon) | 264K | — | ran fine |
| reduce/merge-map (349K chars, almost all Latin JSON) | 349K | — | ran fine |

So a mostly-Latin **JSON** payload of 349K chars fits where a **Hebrew** payload of 611K does not:
**budget per script, not per byte.** The failure message is explicit but only arrives after dispatch —
`Codex ran out of room in the model's context window. Start a new thread…`.

## Trap 2 — `wc -c` is BYTES, not characters

Hebrew is 2 bytes/char plus a separate mark, so `wc -c` inflates: the same prompt read **379K**
via `wc -c` and **273K** via `len(open(f).read())`. Comparing the `wc -c` number against an earlier
char count produced the conclusion that the prompt had grown ~40% (nearly re-designing the chunking
around a phantom) when the real growth was 3.5%. **Always size with**

```
python3 -c 'import sys; print(len(open(sys.argv[1], encoding="utf-8").read()))' PATH
```

never `wc -c`, when the text is not ASCII. Sibling of zsh does not word-split an unquoted `$VAR` (→skill:cc-harness-ops) — same fix
shape: measure it in Python, not in the shell.

## Trap 3 — the advertised window is not the budget, and the agent preamble is already spending it

`~/.codex/models_cache.json` is the record the runtime resolves against, and its `context_window`
reads like the budget. It is not. Two other fields on the SAME record subtract from it before your
payload is considered, and nothing errors if you miss them:

- **`effective_context_window_percent`** — 95 for `gpt-5.6-sol`, so the usable window is
  `272000 × 0.95 = 258400`, not 272000.
- **`base_instructions`** — the Codex agent system prompt, shipped on every call. 17,766 bytes ≈
  **3,576 tokens** (`cl100k_base`). Note it tokenizes at ~5.0 B/token: it is plain English, so it is
  *cheaper* than its byte count suggests, unlike everything else on this page. Guessing it from
  bytes at a JSON/non-Latin ratio over-estimates by ~40%.

Missing the first field alone moved a real acceptance verdict from "+28.4% margin, passes" to
"+23.2%, fails", and that wrong verdict was propagated into two teammates' work and a committed doc
before it was caught. **When a budget decision rests on the window, read the whole model record, not
the one field named `context_window`.**

There is also **no `tokenizer` or `encoding` field anywhere on the record** — so no tokenizer is
authoritative for this model, and the two plausible candidates disagree by up to **51%** on
non-Latin/JSON content (one artifact measured 48,720 tokens under `o200k_base` and 73,465 under
`cl100k_base`). Report a range across both, or pick the higher-count one deliberately and say so;
a single point estimate here is a guess wearing a number's clothes. This is Trap 1 one level up —
script density varies, and so does the tokenizer you are assuming.

## How to size safely

1. **Calibrate against a prompt that actually RAN** on that exact model, in *characters of that
   script*, not a generic token ratio. Keep the known-good number next to the builder.
2. **Probe with one throwaway job before fanning out.** A context overflow terminates within
   ~5-10 s of the turn starting, so a single test dispatch answers "does this size fit?" for the
   cost of one job instead of a whole failed parallel round.
3. When a payload mixes script + JSON, remember they have very different densities — an estimate
   built on the JSON half will under-count the script half.
