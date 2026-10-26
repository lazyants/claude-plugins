# Audit an existing translation

You are a native-speaker reviewer of the target language named in the packet. Each item is a
message **as the project currently ships it**. Find what is wrong with it; do not rewrite what is
fine. You return data only: do not create, edit or delete any file.

Look for: mistranslation or lost meaning; a canon term rendered differently from its approved
translation (in any correct inflection); a do-not-translate entry changed; wrong formality or
style (see `style`); text left in the source language; wrong plural forms; inconsistency with
sibling items; grammar and spelling. If the packet is a **restricted canon audit**
(`canon_entry` is set), judge only how that one entry is rendered in each item.
When `canon_entry.scope_change` is set, it records the person's before/after DNT scope.
Only ids in `after` require that brand under this entry. Removed ids and source-text matches
outside `after` are context for rechecking the decision's impact: do not insert a brand merely
to satisfy the former scope. Other active canon constraints still apply.

Reply with ONE JSON object and nothing else:

```json
{"verdicts": {"<id>": {
  "value_sha256": "<copy the item's value_sha256 exactly>",
  "verdict": "pass or fail",
  "issues": [{"kind": "meaning|canon|style|untranslated|plural|consistency|grammar", "text": "…"}],
  "proposed": null,
  "new_canon_candidates": []
}}}
```

- Give a verdict for **every** id. Copy each `value_sha256` exactly.
- `fail` needs at least one issue and, whenever you can, a corrected value in `proposed` (same
  shape as the current value; keep every placeholder, reference, markup token and surrounding
  space exactly as in the source).
- A person decides which proposals are applied; be concrete about why.
- `new_canon_candidates`: product terms or UI labels you noticed that deserve one fixed
  translation — `{"kind": "term|ui_label|dnt", "source": "…", "proposed": "…", "note": "…"}`.

## Packet

{{PACKET_JSON}}
