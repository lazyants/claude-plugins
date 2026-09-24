# Translate a batch of UI messages

You are translating user-interface messages of a software project from the source language into
the target language named in the packet below. You return data only: do not create, edit or
delete any file.

The packet (JSON) contains:

- `locale` — the target language; `style` — its formality and house-style notes. Follow them in
  every message (the formality decides how the user is addressed).
- `canon` — approved translations of product terms and UI labels, and do-not-translate entries.
  Use the approved translation of a term wherever the term occurs, inflected as the target
  grammar requires. Keep every do-not-translate entry exactly as written.
- `items` — the messages to translate. Each has an `id`, the `source` text (a string, or
  `{"forms": [...]}` for a plural message), and `context` (file, key, comment, maximum length).
  A plural item also has `target_labels`: the forms the target language needs, in order, each
  with a `label` and whether it covers exactly one count (`exact`). Return exactly that many
  forms, in that order.
- In a fix round an item also carries `previous` (the value that failed) and `problems` (why).
  Fix exactly those problems.

Rules for every value:

- Keep every placeholder, reference and markup token exactly as it appears in the source —
  anything in braces, `@:`-style references, tags. Do not translate, rename or re-order what is
  inside them unless the target grammar needs a different order.
- Keep leading and trailing spaces exactly as in the source.
- Use the siblings in the batch for consistency: the same thing is named the same way.
- A button or menu label stays short; respect a `max_length` when one is given.
- Never leave a value empty, and never return the source text unchanged unless it is a
  do-not-translate entry or a proper name.

Reply with ONE JSON object and nothing else:

```json
{"translations": {"<id>": "translated text", "<plural id>": {"forms": ["…", "…"]}}}
```

Include every id from the packet, and no other id.

## Packet

{{PACKET_JSON}}
