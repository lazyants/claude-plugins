# Lazy Ants — Claude Code plugins

Public plugins for [Claude Code](https://claude.com/claude-code). Choose a plugin, install it, then ask Claude to use it in your project.

## Plugins

| Plugin | Version | What it does |
|---|---|---|
| [`ai-cli-optout`](#ai-cli-optout--v121) | 1.2.1 | Apply privacy opt-outs for AI tools and operating systems. |
| [`db-guardrails`](#db-guardrails--v110) | 1.1.0 | Block accidental destructive database commands and set up database safeguards. |
| [`obsidian-project-vault`](#obsidian-project-vault--v110) | 1.1.0 | Create and maintain an Obsidian knowledge base for your project. |
| [`cc-usage-coach`](#cc-usage-coach--v110) | 1.1.0 | Explain your Claude Code usage and suggest ways to spend fewer tokens. |
| [`enduser-handbook`](#enduser-handbook--v1190) | 1.19.0 | Write and maintain a user handbook with screenshots from the running app. |
| [`literary-translator`](#literary-translator--v12221) | 1.222.1 | Translate books with a shared glossary, review, and resumable work. |
| [`multi-profile-plugins`](#multi-profile-plugins--v152) | 1.5.2 | Diagnose Claude/Codex profile isolation and report usage limits across accounts. |
| [`software-localizer`](#software-localizer--v035) | 0.3.5 | Translate application strings or audit existing translations. |

## Install / update / uninstall

Add the marketplace once, then replace `<plugin-name>` with a name from the table:

```sh
claude plugin marketplace add lazyants/claude-plugins
claude plugin install <plugin-name>@lazyants
```

Restart Claude Code after installation so new skills register. Keep the `@lazyants` suffix when updating or removing a plugin:

```sh
claude plugin update <plugin-name>@lazyants
claude plugin uninstall <plugin-name>@lazyants
```

## `ai-cli-optout` — v1.2.1

Ask: “Disable telemetry for my installed AI tools.”

Applies documented privacy settings and explains changes before making them. Some Claude Code opt-outs can hide features; Ollama cloud opt-outs disable cloud services. Prompts and outputs still go to your configured provider.

[Vendor coverage and warnings](docs/plugin-guide.md#ai-cli-optout) · [Skill](plugins/ai-cli-optout/skills/ai-cli-optout/SKILL.md)

## `db-guardrails` — v1.1.0

Ask: “Protect this project's database.”

The command-blocking hook activates on installation. Run the skill to add database privilege separation, framework guards, and test isolation. The hook is a heuristic; database permissions provide additional protection. Requires `awk` and either `jq` or `python3`; missing tools leave the hook inactive.

[Setup, dependencies, and limits](plugins/db-guardrails/README.md)

## `obsidian-project-vault` — v1.1.0

Ask: “Set up an Obsidian vault for this project.”

Create or migrate a vault, ingest sources, query the knowledge base, and check links and structure. Keeps raw sources, synthesized wiki pages, and schema separate.

[Workflow and setup](docs/plugin-guide.md#obsidian-project-vault) · [Skill](plugins/obsidian-project-vault/skills/obsidian-project-vault/SKILL.md)

## `cc-usage-coach` — v1.1.0

Ask: “Why am I hitting my Claude Code usage limit?”

Analyzes local session logs and produces a personalized report with suggested changes. The scripts run locally, but generating the report sends the signal pack and selected prompt excerpts to your Claude model. Raw datasets and local indexes must stay private.

[Usage and privacy](plugins/cc-usage-coach/README.md)

## `enduser-handbook` — v1.19.0

Ask: “Write the end-user handbook for this app.”

Builds tutorials, how-to guides, reference, and explanations from the running UI. A project profile controls language, screenshots, and publishing to an Obsidian vault or plain Markdown. Start with one chapter, then check coverage against the app.

[Authoring guide](docs/plugin-guide.md#enduser-handbook) · [Skill](plugins/enduser-handbook/skills/enduser-handbook/SKILL.md)

## `literary-translator` — v1.222.1

Ask: “Translate this EPUB from French to German.”

Translates and reviews books in segments, with consistent names, configurable verse handling, resumability, and optional Obsidian output. Supports Gutenberg-style EPUBs and an expert-mode custom extractor. Experimental: every new source requires a smoke test, source adapters still need end-to-end pilot validation, and plain-text input is not implemented.

[Setup, workflow, and limitations](docs/plugin-guide.md#literary-translator) · [Skill](plugins/literary-translator/skills/literary-translator/SKILL.md)

## `multi-profile-plugins` — v1.5.2

Ask: “Check my Claude and Codex profiles,” or “How much usage limit is left?”

Diagnoses shared plugin stores and configuration paths that cross profile boundaries. Includes read-only health checks and the `code-limit` usage report. Profile migrations remain manual; the report never redeems reset vouchers.

[Profiles, diagnostics, and usage reports](docs/plugin-guide.md#multi-profile-plugins)

## `software-localizer` — v0.3.5

Ask: “Translate this app into German,” or “Audit our German translation.”

Translates new or changed UI strings, or proposes fixes to existing translations for approval. Uses a shared terminology canon, checked against how the project's field names each term, plus review, project checks, and export safeguards that preserve human edits. Requires Codex for translation and a project-specific adapter; no message-format adapter ships with the plugin.

[Setup and supported scope](plugins/software-localizer/README.md)

## Release notes and license

[Changelog](CHANGELOG.md) · [Literary translator changelog](plugins/literary-translator/CHANGELOG.md) · [MIT license](LICENSE) · [Disclaimer](DISCLAIMER.md)
