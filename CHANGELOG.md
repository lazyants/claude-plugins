# Changelog

All notable changes to `lazyants/claude-plugins` are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is per-plugin, not repo-wide.

## [obsidian-project-vault 1.0.0] — 2026-04-28

Initial release. Promotes the in-house `obsidian-project-vault` skill (previously a personal-scope skill at `~/.claude/skills/`) into a marketplace plugin so it can be installed and updated via `claude plugin install obsidian-project-vault@lazyants`.

### Added
- `plugins/obsidian-project-vault/skills/obsidian-project-vault/SKILL.md` — LLM Wiki pattern, three-layer architecture (raw sources / wiki / schema), four setup modes (create, migrate, audit, ingest), Report template + frontmatter, INDEX.md navigation, CLAUDE.md workflow integration, query-and-file-back loop, vault-lint operation.
- `plugins/obsidian-project-vault/skills/obsidian-project-vault/references/obsidian-tips.md` — human-side Obsidian workflow notes (Web Clipper, Dataview queries, graph view).

## [ai-cli-optout 1.1.0] — 2026-04-24

Adds Vercel CLI and generalizes the CLI-command opt-out schema so adjacent developer CLIs can slot in without bespoke fields.

### Added
- `vendors/vercel.json` — Vercel CLI. Two documented opt-outs, both shipped: `vercel telemetry disable` subcommand (persistent — writes `collectMetrics=false` to the XDG config file cross-platform) and `VERCEL_TELEMETRY_DISABLED=1` env var (per-run override only — does NOT change the persisted status, per vendor docs). `persistent_files[]` surfaces config + auth paths for macOS, Linux, and Windows (`%APPDATA%\Roaming\xdg.data\com.vercel.cli\`) for review — never deleted.
- `cli_commands[]` schema field and test-suite invariant (`cmd` + `disables` non-empty).

### Changed
- `vendors/copilot.json` — `gh_config_commands[]` → `cli_commands[]`. Semantics unchanged; the old name was specific to `gh config set`, the new name covers the generic "vendor-blessed CLI opt-out command" pattern (Vercel's `vercel telemetry disable`, future equivalents).
- `SKILL.md` Step 3 (c2) rewritten to describe generic `cli_commands[]` with examples for both GitHub and Vercel.
- Vendor matrix in `SKILL.md` extended with a Vercel row; frontmatter triggers add `"disable vercel telemetry"`, `"opt out of vercel"`, `"vercel privacy"`.

### Notes
- Next.js and Turborepo are explicitly **not** covered. Both are Vercel-owned but ship separate telemetry streams with documented opt-outs (`NEXT_TELEMETRY_DISABLED=1` / `next telemetry disable`; `TURBO_TELEMETRY_DISABLED=1` / `DO_NOT_TRACK=1` / `turbo telemetry disable`). Adding them requires separate vendor files — deferred until requested.
- Test count after this release: 357 assertions across 2 files (was 330 in 1.0.3); delta is the new `cli_commands` shape assertion running against every vendor plus all existing assertions running against the new `vercel.json`.

## [ai-cli-optout 1.0.3] — 2026-04-24

First public-ready release. Pre-publish blockers from 0.1.0 closed.

### Fixed
- **B1** — `vendors/phpstorm.json` `detect_paths` narrowed to PhpStorm-specific locations (`/Applications/PhpStorm.app` and `~/Applications/JetBrains Toolbox/Apps/PhpStorm`). The shared `~/Library/Application Support/JetBrains` ancestor — matched by every JetBrains IDE — is gone. A regression guard in `tests/vendor-schema.test.sh` blocks re-introduction of any shared / ancestor path in any vendor JSON.

### Added
- `plugins/ai-cli-optout/tests/` — 182 assertions across 2 files. `vendor-schema.test.sh` covers JSON validity, required-field shape, dotted-path edit keys, `manual_only` invariants (zero reachable auto-edit entries by construction), `shell_commands[]` platform-gating, and the B1 regression guard. `scripts.test.sh` smoke-tests both shipped bash scripts with isolated fake-HOME and `file://` fixtures — no network required.

## [ai-cli-optout 0.1.0] — 2026-04-24

Initial scaffold. Not publicly released.

### Added
- 11 vendor configs: Anthropic Claude Code, OpenAI Codex CLI, Google Gemini CLI, GitHub Copilot CLI + `gh`, Cursor (manual-only), Cursor CLI, Google Antigravity, VS Code, PhpStorm (manual-only), macOS system privacy, Windows system privacy.
- Platform-gated execution: dormant vendors render as copy-paste on the wrong OS, never auto-execute.
- Research script (`scripts/check_new_optouts.sh`) — diffs live vendor docs against baseline to surface newly documented env vars / settings keys.
- Persistent-files report (`scripts/report_persistent_files.sh`) — lists local state (session logs, caches, OAuth tokens) without deleting.
- Provider switches documented (Bedrock / Vertex / Foundry) — surfaced only on explicit user request; never auto-applied.
