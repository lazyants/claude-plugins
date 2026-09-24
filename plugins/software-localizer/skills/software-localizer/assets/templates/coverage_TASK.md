# Check that the adapter collects every user-visible string

A per-project adapter collects the project's translatable strings. The packet lists the files
the adapter reads with the ids it collected from each, and an independent inventory of every
file in the project. Find user-visible, translatable text the adapter **missed**. You may read
any file under the project root; you return data only: do not create, edit or delete any file.

Check:

1. In each file the adapter reads: any string a user would see that is not among the collected
   ids.
2. In the inventory: any other file that holds translatable UI or message text for this project
   (another catalog, a per-locale file, an inline per-language map) that the adapter does not
   read at all.

Ignore code identifiers, log and debug messages, test fixtures, and text the project renders
only in the source language on purpose (say so in `why_user_visible` if unsure).

Reply with ONE JSON object and nothing else:

```json
{"missing": [{"file": "relative/path", "key": "the key or null for a whole missed file", "why_user_visible": "…"}]}
```

An empty list means nothing was missed.

## Packet

{{PACKET_JSON}}
