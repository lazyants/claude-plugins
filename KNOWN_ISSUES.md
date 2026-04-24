# Known Issues

Tracked caveats and planned coverage for `ai-cli-optout`. Resolved blockers are in [`CHANGELOG.md`](./CHANGELOG.md), not here.

## Documented caveats (not bugs)

### C1 — Apple Intelligence no-op on non-MDM Macs
`com.apple.applicationaccess allow*` keys are MDM-only; `defaults write` silently succeeds but does not change state on unmanaged Macs. The skill surfaces these as `manual_only_items[]` with the System Settings path — that's the current supported workaround.

### C2 — Opus 4.6 1M silently disabled
On Anthropic Max / Team / Enterprise plans, setting `DISABLE_TELEMETRY=1` (or `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`) silently disables the Opus 4.6 1M-context default model — feature entitlements and telemetry opt-out share the same GrowthBook kill-switch.
Upstream: <https://github.com/anthropics/claude-code/issues/34178>.
The skill does not work around this; users on eligible plans should weigh the trade-off.

### C3 — Windows Home: `AllowTelemetry=0` unsupported
Floor is `Required`. `windows-privacy.json` reflects this; no fix planned.

## Planned coverage additions

- **Vercel Claude Code plugin** — `VERCEL_PLUGIN_TELEMETRY=off` env var disables bash-command telemetry to `telemetry.vercel.com`. Needs detection that distinguishes the Vercel plugin from other Claude Code plugins; tracked for a follow-up minor release.
- **Windsurf / Codeium**, **Zed**, **Ollama** — under consideration; PRs welcome.
- **Linux privacy surfaces** (flatpak reports, GNOME / KDE feedback agents) — not scoped.
