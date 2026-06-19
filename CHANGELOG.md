# Changelog

All notable changes to `lazyants/claude-plugins` are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is per-plugin, not repo-wide.

## [enduser-handbook 1.0.1] — 2026-06-19

Documentation-only release. Hardens the screenshot-capture guidance in the skill; no behavioral or schema change to the plugin.

### Changed
- `references/capture-safety.md` — the PII-masking guidance now mandates *reproducible* masking: mask in-step (including control/header values), assert no leak with a fail-closed check, and scope both the mask and the leak-assert to the screenshot frame rather than a DOM subtree (a transparent backdrop can bleed un-masked content from the page behind a modal). Always keep an eyeball-confirmation shot.
- `references/container-isolation.md` — added an engine-agnostic "Common command patterns" section (run as the host user, keep engine caches out of the bind-mounted repo, join the existing network instead of recreating services, pin the engine image in lockstep with the test dependency). Concrete per-project commands still live in the project's `capture.command` / `.claude/handbook/capture-recipe.md`.
- `assets/handbook.profile.example.yml` — the example `capture.command` demonstrates the patterns above and pins `LANG`/`LC_ALL` to match `capture.locale`, satisfying the locale guarantee in `container-isolation.md`.

## [cc-usage-coach 1.0.0] — 2026-06-18

Initial release. New plugin — personalized, behavior-aware analysis of where your Claude Code (Max/Pro) usage-limit tokens go, with ranked, low-effort ways to use fewer, computed entirely from your local session logs. Python measures; Claude concludes.

### Added
- `plugins/cc-usage-coach/skills/cc-usage-coach/SKILL.md` — the skill that drives the scripts and writes the personalized report from the signal pack.
- `scripts/extract.py` — scans local Claude Code session logs into a local `dataset/`.
- `scripts/signals.py` — emits `signal_pack.json` (path-free AND project-name-free — project labels are opaque IDs — safe to share) plus two local-only maps: `source_index.json` (opaque `source_ref` → real file) and `project_index.json` (opaque project ID → real project name).
- `scripts/arc.py <source_ref>` — inspects a single session's prompt arc (local-only).
- Local-first by construction: no network calls; `source_index.json`, `project_index.json`, `dataset/`, and the `arc.py` digest are local-only (real paths, project names + prompt text, `0600` where applicable, never uploaded). Honors `CLAUDE_CONFIG_DIR`; extra scan roots via `CC_COACH_CONFIG_DIRS`; output location via `CC_COACH_OUT` (else next to the scripts if writable, else `${XDG_CACHE_HOME:-~/.cache}/cc-usage-coach/`).
- `tests/` — pytest suite over synthetic fixtures (no real logs, no network) covering the extractor, the signal-pack shape and its path-free + project-name-free guarantee, the per-session arc, and fixture safety. Run with `bash tests/run-all.sh`.

## [enduser-handbook 1.0.0] — 2026-06-18

Initial release. New plugin for generating end-user handbooks across projects (German/„Sie", English, any register; Laravel/Vue, Django/React, etc.) from a per-project `.claude/handbook/profile.yml`.

### Added
- Methodology lifted from VPP-handbook (Diátaxis, anti-fabrication, capture safety, glossary discipline, completeness gate); project-specific bits (language, stack, capture command, publish target) are profile-driven.
- v1 ships the `obsidian_vault` publish-target adapter; Confluence/GitBook/Docusaurus targets are an additive future change.

## [db-guardrails 1.0.0] — 2026-05-22

Initial release. New plugin — protects databases from accidental destructive commands run by AI coding agents. Generalised from a four-layer guardrail stack built in-house after an agent twice wiped a development database via a misrouted `artisan migrate`.

### Added
- `plugins/db-guardrails/hooks/block-destructive-db.sh` + `hooks/hooks.json` — always-on `PreToolUse:Bash` hook (layer 4). Framework-agnostic: blocks raw SQL (`DROP`, `TRUNCATE`, `DELETE` without `WHERE`), Laravel, Rails, Django, Prisma, TypeORM, Sequelize, Knex, Drizzle, Doctrine/Symfony, EF Core, Alembic, Flyway, Liquibase, MongoDB, Redis, plus `docker compose down -v` and `rm -rf` of DB data directories. Out-of-band bypass via `ALLOW_DESTRUCTIVE_DB_HOOK=true`; no inline self-bypass. Written for bash 3.2+; `jq`/`python3` payload parsing with a fail-open-with-warning fallback.
- `plugins/db-guardrails/skills/db-guardrails/SKILL.md` — `/db-guardrails` installer skill. Detects database engine + framework, scaffolds layers 1–3.
- `assets/` — layer 1 privilege separation for MySQL/MariaDB (`mariadb`/`mysql` client auto-detected) and PostgreSQL; layer 2/3 drop-in guard files for Laravel, Django, Rails and Symfony.
- `references/framework-guards.md` — per-framework boot-guard placement notes, plus the Node-ORM connection-string-split config pattern and the MongoDB scoped-role recipe.
- `tests/block-destructive-db.test.sh` — 28 assertions covering blocked commands, legitimate look-alikes (`truncate -s 0`, `php artisan migrate`, `DELETE ... WHERE`, `rm -rf node_modules`), and the bypass env var.

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
