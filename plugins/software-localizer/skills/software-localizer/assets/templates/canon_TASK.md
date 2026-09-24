# Propose the canon: terms, UI labels and do-not-translate entries

You read a software project's source-language UI messages (and, when present, its current
translations) and propose the entries that must be translated **the same way everywhere**. You
return data only: do not create, edit or delete any file.

Propose:

- `term` — product vocabulary a user must recognise across screens ("workspace", "billing
  cycle", a domain concept).
- `ui_label` — names of buttons, menus, screens and settings that other messages refer to
  ("open Settings → Privacy" must use the same label as the Settings screen).
- `dnt` — do not translate: brand and product names, identifiers, units that stay as written.

Do not propose ordinary words that happen to repeat. Prefer a short, high-value list: a person
approves it before translation starts.

For each entry give the ids where it occurs and, per target language in the packet, a proposed
translation; when the packet carries current translations, also list every **current** rendering
you found (so inconsistent renderings show up side by side).

Reply with ONE JSON object and nothing else:

```json
{"candidates": [{
  "kind": "term|ui_label|dnt",
  "source": "Workspace",
  "note": "why it matters",
  "occurrences": ["<id>", "…"],
  "translations": {"<locale>": {"proposed": "…", "current": ["…"]}}
}]}
```

## Packet

{{PACKET_JSON}}
