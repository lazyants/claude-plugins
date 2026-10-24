# Check that the adapter collects every user-visible string

A per-project adapter collects the project's translatable strings. The packet lists the files
the adapter reads with the ids it collected from each. Its `inventory.path` points to an
independent inventory of every file in the project: one project-relative path per line,
`inventory.line_count` lines in total. Read that file with file-read tools; for a large inventory,
read it in portions until you have examined the full list. Find user-visible, translatable text
the adapter **missed**. You may read the inventory file and any file under `project_root`; you
return data only: do not create, edit or delete any file.

The packet's `workspace_root` is the localization tool's own directory: its term canon, adapter and
run files. It may sit under `project_root`. It is not the project's UI text and it is not in the
inventory. Never report a file under it.

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
{"run_id": "<copy the packet's run_id exactly>",
 "missing": [{"file": "relative/path", "key": "the key or null for a whole missed file", "why_user_visible": "…"}]}
```

An empty list means nothing was missed. Copy `run_id` from the packet exactly: an answer for a
different run of the adapter check is refused.

## Packet

{{PACKET_JSON}}
