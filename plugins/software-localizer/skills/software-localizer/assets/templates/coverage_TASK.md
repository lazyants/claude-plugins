# Check that the adapter collects every user-visible string

A per-project adapter collects the project's translatable strings. The packet lists the files
the adapter reads with the ids it collected from each. Its `inventory.path` points to an
independent inventory of every file in the project: one project-relative path per line,
`inventory.line_count` lines in total, bound by `inventory.sha256`. Find user-visible,
translatable text the adapter **missed**.

Read `{{INVENTORY_PROCEDURE_PATH}}`. You may read a small inventory directly. For a large one,
use its exhaustive programmatic classification procedure: code reads **every** path, retains
one classification per path, and gives you a structured group index and all unresolved candidate
paths. The raw inventory need not enter model context in chunks. Review each group's evidence
and inspect every unresolved candidate; neither a directory/extension rule nor membership in
the adapter's file list proves that a path has no missed strings. Keep unexpected and hidden
catalogs visible. The procedure uses ordinary Python and shell/file tools; no MCP is required.

You may read the inventory and any file under `project_root`. You may create classification
evidence only in a new directory beside `inventory.txt`, retaining its run ID, inventory hash,
path count, full mapping, group index and candidate list. Do not change project files, the
inventory, packet, adapter, canon or acceptance state. Return the JSON answer only.

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

An empty list means nothing was missed, and is allowed only after every path is accounted for
and every candidate is resolved by inspection or an explicit, evidence-backed scope decision.
If a candidate cannot be inspected or its scope remains uncertain, include it in `missing`
with `key: null` and explain the gap in `why_user_visible`; do not certify coverage. A mapping
or inventory-integrity failure stops the turn: report the failure to the caller instead of
returning an empty list. Copy `run_id` from the packet exactly: an answer for a different run
of the adapter check is refused. The existing missing-file/operator scope contract still applies.

## Packet

{{PACKET_JSON}}
