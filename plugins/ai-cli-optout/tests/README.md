# ai-cli-optout — tests

Static invariant + smoke tests. Run before every release.

```
bash tests/run-all.sh
```

## What's covered

- **`vendor-schema.test.sh`** — static invariants across every `vendors/*.json`:
  - required fields present, types correct
  - no shared / ancestor `detect_paths` (B1 regression guard)
  - dotted-path `edits[].key` syntax
  - `manual_only: true` vendors have `manual_instructions` + `process_check` and zero auto-edit entries
  - `shell_commands[]` is always `platforms`-gated
  - `platforms` values restricted to `darwin` / `linux` / `win32`
- **`scripts.test.sh`** — smoke tests for the shipped bash scripts:
  - `report_persistent_files.sh`: empty fake HOME reports `(not present)`; populated fake HOME reports a size; unknown vendor exits 2
  - `check_new_optouts.sh`: fetches a `file://` fixture doc, surfaces a new token in the "Not in baseline" section, and does not flag baseline tokens

## What's **not** covered (and why)

- Whether Claude actually honors `manual_only`, `platforms`, and caveats at runtime — those are instruction-level behaviors in `SKILL.md`, not code. The schema tests guarantee the data is shaped so those pathways are reachable / unreachable by construction.
- Network fetches against live vendor docs — `check_new_optouts.sh` is tested via `file://` to stay deterministic. Real doc churn is surfaced by running the script against live docs as a release step, not a test.
- Platform-specific command execution (`defaults write`, `reg add`) — never run by the tests. Platform gating is asserted at the data level only.

## Requirements

`jq` and `curl` on PATH. macOS / Linux. Tests create `$TMPDIR/ai-cli-optout-test.*` and clean up on exit.
