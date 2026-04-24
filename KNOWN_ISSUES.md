# Known Issues

Tracked bugs, caveats, and pre-publish blockers for `ai-cli-optout`.

## Pre-publish blockers (v0.1.0 → v0.2.0)

These gate the flip of this repo from private to public. Do not install this plugin from a published marketplace until they are closed.

### B1 — `detect_paths` false-positive on shared config dirs
`vendors/phpstorm.json` declares `detect_paths` at a generic JetBrains configuration directory that is shared with every other JetBrains IDE (IntelliJ, WebStorm, RubyMine, etc.). A user running this skill with any JetBrains IDE installed but without PhpStorm hits a false detection.

**Fix:** narrow the path to a PhpStorm-specific subdirectory (`.../PhpStorm<version>/options/`), and/or require the binary check (`detect_cmd: phpstorm`) to succeed.

### B2 — No fixture tests
Every `vendors/*.json` and both `scripts/*.sh` ship without tests. Minimum bar before public release (per adversarial review):
- Per-vendor JSON fixture: baseline parses, every `detect_paths` / `detect_cmd` is well-formed, `edits[].key` uses dotted paths only.
- Script fixture: `check_new_optouts.sh` and `report_persistent_files.sh` run against a frozen fake-home tree and produce deterministic output.
- Dormant-platform fixture: proves Windows commands never run on `darwin`/`linux`, macOS `defaults write` never runs on `win32`.
- Manual-only fixture: proves Cursor / PhpStorm vendors never get auto-edited even when the CLI is running.

## Documented caveats (not bugs)

### C1 — Apple Intelligence no-op on non-MDM Macs
`com.apple.applicationaccess allow*` keys are MDM-only; `defaults write` silently succeeds but does not change state on unmanaged Macs. The skill surfaces these as `manual_only_items[]` with the System Settings path — that's the current supported workaround.

### C2 — Opus 4.6 1M silently disabled
On Anthropic Max/Team/Enterprise plans, setting `DISABLE_TELEMETRY=1` (or `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`) silently disables the Opus 4.6 1M-context default model — feature entitlements and telemetry opt-out share the same GrowthBook kill-switch.
Upstream: <https://github.com/anthropics/claude-code/issues/34178>.
The skill does not work around this; users on eligible plans should weigh the trade-off.

### C3 — Windows Home: `AllowTelemetry=0` unsupported
Floor is `Required`. `windows-privacy.json` reflects this; no fix planned.

## Planned coverage additions (v0.2.0+)

- **Vercel Claude Code plugin** — `VERCEL_PLUGIN_TELEMETRY=off` env var disables bash-command telemetry to `telemetry.vercel.com`. Deferred until fixture-test harness exists to validate detection (same trap as B1).
- **Windsurf / Codeium**, **Zed**, **Ollama** — under consideration; PRs welcome.
- **Linux privacy surfaces** (flatpak reports, GNOME/KDE feedback agents) — not scoped.
