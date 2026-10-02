# ai-cli-optout — tests

Static invariant + smoke tests. The full suite runs in GitHub Actions before every merge. Locally run only the changed test file and cheap static checks, as required by the root `CLAUDE.md`.

```
bash tests/run-all.sh
```

## What's covered

- **`vendor-schema.test.sh`** — static invariants across every `vendors/*.json`:
  - required fields present, types correct
  - no shared / ancestor / retained-state `detect_paths` (B1 regression guard)
  - dotted-path `edits[].key` syntax
  - `manual_only: true` vendors have manual instructions and zero auto-edit entries; declared process checks are validated
  - platform-specific settings paths, install/process checks and literal versus nested key modes
  - discovery patterns contain no backticks and Codex profile scopes are structured
  - `shell_commands[]` is always `platforms`-gated
  - `platforms` values restricted to `darwin` / `linux` / `win32`
- **`scripts.test.sh`** — smoke tests for the shipped bash scripts:
  - `report_persistent_files.sh`: literal and glob paths, multiple versions, spaces, native/WSL Windows path conversion and explicit unresolved/foreign-platform skips
  - `check_new_optouts.sh`: deterministic `file://` docs, real shipped Anthropic vendor data, baseline subtraction and new-token discovery
- **`new-vendors.test.sh`** — executes shipped inventory checks and command strings against isolated CLI fixtures: exact enabled Vercel registrations, Windsurf/Codeium detection, Flatpak capability/scope checks and literal PowerShell arguments. It never changes real account, app or system settings.

## What's **not** covered (and why)

- Whether Claude actually honors `manual_only`, `platforms`, and caveats at runtime — those are instruction-level behaviors in `SKILL.md`, not code. The schema tests guarantee the data is shaped so those pathways are reachable / unreachable by construction.
- Network fetches against live vendor docs — `check_new_optouts.sh` is tested via `file://` to stay deterministic. Real doc churn is surfaced by running the script against live docs as a release step, not a test.
- Platform-specific command execution (`defaults write`, `reg add`) — never run by the tests. Platform gating is asserted at the data level only.

## Requirements

`jq` and `curl` on PATH. macOS / Linux. Tests create `$TMPDIR/ai-cli-optout-test.*` and clean up on exit.
