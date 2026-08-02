# Sizing a prompt against the context window (non-Latin script / JSON payloads)

Fires when sizing a prompt — whole-corpus review, consolidation, map-reduce chunking — whose payload
is non-Latin (vocalized/pointed Hebrew, Arabic with harakat, Devanagari, heavy CJK) or is mostly JSON.
Two independent traps, both silent until a job dies.

## Trap 1 — `chars/4` is a Latin-prose ratio, not a universal one

Pointed Hebrew carries a combining niqqud mark per letter; those marks tokenize separately, so the
real chars-per-token is far below 4. Measured on SSK vol.2 (`gpt-5.6-sol`, 272K context):

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
python3 -c "print(len(open(p).read()))"
```

never `wc -c`, when the text is not ASCII. Sibling of [[gotcha-zsh-no-word-splitting]] — same fix
shape: measure it in Python, not in the shell.

## How to size safely

1. **Calibrate against a prompt that actually RAN** on that exact model, in *characters of that
   script*, not a generic token ratio. Keep the known-good number next to the builder.
2. **Probe with one throwaway job before fanning out.** A context overflow terminates within
   ~5-10 s of the turn starting, so a single test dispatch answers "does this size fit?" for the
   cost of one job instead of a whole failed parallel round.
3. When a payload mixes script + JSON, remember they have very different densities — an estimate
   built on the JSON half will under-count the script half.
