# Lazy Ants — Claude Code plugins

Public plugins for [Claude Code](https://claude.com/claude-code), maintained under the `Lazy Ants` brand.

## Plugins

### `ai-cli-optout` — v1.1.0

Opts out of telemetry, error reporting, analytics, feedback surveys, and related data collection across every locally installed AI CLI and AI-enabled IDE, plus Vercel CLI (adjacent developer tooling) and macOS / Windows OS-level privacy surfaces. One skill, twelve vendors, data-driven. 369 test assertions guard vendor-schema invariants and script behavior.

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
| Vercel CLI | any | `vercel telemetry disable` (persistent) + `VERCEL_TELEMETRY_DISABLED=1` (per-run) |
| macOS system privacy | darwin | `defaults write` (AdLib, CrashReporter) |
| Windows system privacy | win32 | `reg add` (Recall, Copilot, Telemetry, AdvertisingInfo) |

#### Install

```
claude plugin marketplace add lazyants/claude-plugins
claude plugin install ai-cli-optout@lazyants
```

Restart Claude Code once for the new skill triggers to register. Then ask Claude to "disable telemetry", "opt out of telemetry", "privacy mode", etc. — the full trigger list is in `plugins/ai-cli-optout/skills/ai-cli-optout/SKILL.md`.

#### Update / uninstall

```
claude plugin update ai-cli-optout@lazyants
claude plugin uninstall ai-cli-optout@lazyants
```

The `@lazyants` marketplace suffix is required — `claude plugin update ai-cli-optout` alone will not find the plugin.

#### Warnings before you run it

- **Anthropic Claude Code users:** setting `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` or `DISABLE_TELEMETRY=1` does more than stop telemetry — both flags also disable the `/remote-control` command and, on Max / Team / Enterprise plans, silently switch Opus 4.6 off the 1M-context default model. See [anthropics/claude-code#34178](https://github.com/anthropics/claude-code/issues/34178) and `KNOWN_ISSUES.md` §C2. The skill gates both edits behind `requires_confirmation: true` so you see the trade-off and can decline. A safe narrow opt-out that leaves these features intact (`DISABLE_ERROR_REPORTING=1`, `DISABLE_FEEDBACK_COMMAND=1`, `CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1`, `skipWebFetchPreflight: true`) is documented in `KNOWN_ISSUES.md` §C2.
- **Cursor:** quit the app before the skill edits any JSON — Cursor's Electron process rewrites settings on graceful quit and will overwrite your changes.
- **Antigravity:** the AI-training opt-out is **email-only** to `antigravity-support@google.com`; no CLI / setting exists. The skill surfaces this in its report but cannot automate it.
- **Vercel CLI:** the subcommand `vercel telemetry disable` is persistent; `VERCEL_TELEMETRY_DISABLED=1` is per-run only and does **not** change the persisted status reported by `vercel telemetry status`. The skill applies both so you're covered either way.

#### What this does **not** cover

- Prompts and outputs still go to each vendor's API. To avoid that path you must switch providers (e.g. route Claude Code to AWS Bedrock / Google Vertex / Azure Foundry).
- Existing local state (Codex sqlite, conversation logs, OAuth tokens) is **reported**, never deleted.
- Cursor AI telemetry when Privacy Mode is off — the only vendor-blessed control is the UI toggle.

See [`DISCLAIMER.md`](./DISCLAIMER.md) for the full no-warranty statement.

## License & disclaimer

MIT — see [`LICENSE`](./LICENSE). No warranty, no vendor affiliation — see [`DISCLAIMER.md`](./DISCLAIMER.md).
