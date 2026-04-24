# Changelog

All notable changes to `lazyants/claude-plugins` are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is per-plugin, not repo-wide.

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
