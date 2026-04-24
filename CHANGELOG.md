# Changelog

All notable changes to `lazyants/claude-plugins` are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is per-plugin, not repo-wide.

## [ai-cli-optout 0.1.0] — 2026-04-24

Initial scaffold. Not yet published to a public marketplace — see `KNOWN_ISSUES.md` for pre-publish blockers.

### Added
- 11 vendor configs: Anthropic Claude Code, OpenAI Codex CLI, Google Gemini CLI, GitHub Copilot CLI + `gh`, Cursor (manual-only), Cursor CLI, Google Antigravity, VS Code, PhpStorm (manual-only), macOS system privacy, Windows system privacy.
- Platform-gated execution: dormant vendors render as copy-paste on the wrong OS, never auto-execute.
- Research script (`scripts/check_new_optouts.sh`) — diffs live vendor docs against baseline to surface newly documented env vars / settings keys.
- Persistent-files report (`scripts/report_persistent_files.sh`) — lists local state (session logs, caches, OAuth tokens) without deleting.
- Provider switches documented (Bedrock / Vertex / Foundry) — surfaced only on explicit user request; never auto-applied.
