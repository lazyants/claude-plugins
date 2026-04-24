# Lazy Ants — Claude Code plugins

Public plugins for [Claude Code](https://claude.com/claude-code), maintained under the `Lazy Ants` brand.

## Plugins

### `ai-cli-optout` — v1.0.3

Opts out of telemetry, error reporting, analytics, feedback surveys, and related data collection across every locally installed AI CLI and AI-enabled IDE, plus macOS / Windows OS-level privacy surfaces. One skill, eleven vendors, data-driven. 182 test assertions guard vendor-schema invariants and script behavior.

#### Vendors covered (baseline 2026-04-24)

| Vendor | Platform | Kind |
|---|---|---|
| Anthropic Claude Code | any | settings.json + env |
| OpenAI Codex CLI | any | `~/.codex/config.toml` |
| Google Gemini CLI | any | settings.json + env |
| GitHub Copilot CLI + `gh` | any | `gh config set` + env |
| Cursor | darwin | manual only — Cmd+Shift+J → Privacy Mode |
| Cursor CLI (`cursor-agent`) | any | manual only — account-level Privacy Mode |
| Google Antigravity | darwin | settings.json (AI-training opt-out is email-only) |
| VS Code | darwin | settings.json (Copilot extension does not inherit) |
| PhpStorm | darwin | manual only — Settings → Tools → Usage Statistics |
| macOS system privacy | darwin | `defaults write` (AdLib, CrashReporter) |
| Windows system privacy | win32 | `reg add` (Recall, Copilot, Telemetry, AdvertisingInfo) |

#### Install (after public release)

```
claude plugin marketplace add lazyants/claude-plugins
claude plugin install ai-cli-optout@lazyants
```

Trigger the skill by asking Claude to "disable telemetry", "opt out of telemetry", "privacy mode", etc. — the full trigger list is in `plugins/ai-cli-optout/skills/ai-cli-optout/SKILL.md`.

#### Warnings before you run it

- **Anthropic Opus 4.6 1M users:** setting `DISABLE_TELEMETRY` silently disables the 1M-context default model on Max / Team / Enterprise plans. See [anthropics/claude-code#34178](https://github.com/anthropics/claude-code/issues/34178) and `KNOWN_ISSUES.md` §C2.
- **Cursor:** quit the app before the skill edits any JSON — Cursor's Electron process rewrites settings on graceful quit and will overwrite your changes.
- **Antigravity:** the AI-training opt-out is **email-only** to `antigravity-support@google.com`; no CLI / setting exists. The skill surfaces this in its report but cannot automate it.
- **Vercel Claude Code plugin:** if installed, it sends every bash command string to `telemetry.vercel.com` by default. Opt out with `VERCEL_PLUGIN_TELEMETRY=off`. First-class vendor support planned for v0.2.0 (see `KNOWN_ISSUES.md`).

#### What this does **not** cover

- Prompts and outputs still go to each vendor's API. To avoid that path you must switch providers (e.g. route Claude Code to AWS Bedrock / Google Vertex / Azure Foundry).
- Existing local state (Codex sqlite, conversation logs, OAuth tokens) is **reported**, never deleted.
- Cursor AI telemetry when Privacy Mode is off — the only vendor-blessed control is the UI toggle.

See [`DISCLAIMER.md`](./DISCLAIMER.md) for the full no-warranty statement.

## License & disclaimer

MIT — see [`LICENSE`](./LICENSE). No warranty, no vendor affiliation — see [`DISCLAIMER.md`](./DISCLAIMER.md).
