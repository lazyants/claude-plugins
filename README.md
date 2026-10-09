# Lazy Ants — Claude Code plugins

Public plugins for [Claude Code](https://claude.com/claude-code), maintained under the `Lazy Ants` brand.

## Plugins

| Plugin | Version | What it does |
|---|---|---|
| [`ai-cli-optout`](#ai-cli-optout--v120) | 1.2.0 | Privacy opt-outs for AI CLIs and IDEs, Vercel CLI and its Claude Code plugin, plus macOS / Windows / Linux controls. |
| [`db-guardrails`](#db-guardrails--v110) | 1.1.0 | Stop AI coding agents from accidentally emptying your database — an always-on hook across 15+ frameworks, with privilege separation for MySQL/MariaDB, PostgreSQL and SQL Server. |
| [`obsidian-project-vault`](#obsidian-project-vault--v110) | 1.1.0 | Set up, migrate, audit, and operate an Obsidian vault as an LLM Wiki — a persistent, compounding knowledge base maintained by Claude Code. |
| [`cc-usage-coach`](#cc-usage-coach--v110) | 1.1.0 | Personalized, behavior-aware analysis of where your Claude Code (Max/Pro) usage-limit tokens go, with ranked, low-effort ways to use fewer — computed entirely from your local session logs. Python measures; Claude concludes. |
| [`enduser-handbook`](#enduser-handbook--v1183) | 1.18.3 | Author, capture, and publish a Diátaxis-structured end-user handbook for any project — methodology shipped as a reusable skill, project-specific bindings supplied via `.claude/handbook/profile.yml`. |
| [`literary-translator`](#literary-translator--v12221) | 1.222.1 | High-fidelity literary book translation over a Gutenberg-style EPUB source (expert-mode `custom` extractor also supported) — a codex-translate → deterministic false-green gate → codex-review → Claude-fix loop run to convergence, with a frozen name/realia canon, a configurable verse policy, and ledger-based resumability, plus optional book assembly into an Obsidian glossary-wiki behind a deterministic render/diff gate. |
| [`multi-profile-plugins`](#multi-profile-plugins--v150) | 1.5.0 | Understand and diagnose config-profile isolation across multiple Claude Code `CLAUDE_CONFIG_DIR` profiles or Codex `CODEX_HOME` profiles — why profiles that share a plugins store hit recurring "corrupted installLocation" errors and cross-profile plugin deletion, and why a Codex profile seeded by copying `config.toml` keeps reading the home it came from. A read-only health-check script for each, plus a usage-limit report across every profile and home. |
| [`software-localizer`](#software-localizer--v010) | 0.1.0 | Translate a software project into another language, or audit the translation it already has — a canon and glossary decided once, codex translation through script checks and a Claude review, a ledger that never overwrites a person's edit, and a per-project adapter accepted by round-trip and coverage checks. |

> **Changelogs.** Every plugin's release notes are in the root [`CHANGELOG.md`](CHANGELOG.md) — except `literary-translator`, which keeps its own at [`plugins/literary-translator/CHANGELOG.md`](plugins/literary-translator/CHANGELOG.md). The root file is frozen for that plugin at its `1.1.0` entry, so its later releases and its Known limitations are only in the per-plugin file. The per-plugin sections below describe what each plugin does and deliberately carry no per-release history — the changelog is the only place it lives.

## Install / update / uninstall

```
claude plugin marketplace add lazyants/claude-plugins
claude plugin install <plugin-name>@lazyants
```

Restart Claude Code once after install for new skill triggers to register. The `@lazyants` marketplace suffix is required on every plugin command — bare `claude plugin update <name>` will not find the plugin.

```
claude plugin update <plugin-name>@lazyants
claude plugin uninstall <plugin-name>@lazyants
```

## `ai-cli-optout` — v1.2.0

Applies documented opt-outs for telemetry, error reporting, analytics, feedback surveys and related data collection across installed AI CLIs and IDEs, Vercel CLI and its Claude Code plugin, and macOS / Windows / Linux privacy controls. One skill, seventeen vendor entries, data-driven. Automated checks guard vendor schemas, platform paths, installation markers and helper behavior. Notes and platform limits are surfaced before changes; settings edits wait until the app is stopped, and Codex analytics covers existing profile overlays as well as the main config.

Trigger phrases: "disable telemetry", "opt out of telemetry", "privacy mode", etc. — full list in `plugins/ai-cli-optout/skills/ai-cli-optout/SKILL.md`.

### Vendors covered

| Vendor | Platform | Kind |
|---|---|---|
| Anthropic Claude Code | any | settings.json + env *(2 edits confirmation-gated — see warnings)* |
| OpenAI Codex CLI | any | `~/.codex/config.toml` |
| Google Gemini CLI | any | settings.json + env |
| GitHub Copilot CLI + `gh` | any | `gh config set` + env |
| Cursor | darwin | manual only — Cmd+Shift+J → Privacy Mode |
| Cursor CLI (`cursor-agent`) | any | manual only — account-level Privacy Mode |
| Google Antigravity | darwin, linux, win32 | settings.json + separate AI-training controls (see vendor sources) |
| VS Code | darwin, linux, win32 | platform-specific settings.json (Copilot extension does not inherit) |
| PhpStorm | darwin | manual only — Settings → Tools → Usage Statistics |
| Vercel CLI | any | `vercel telemetry disable` (persistent) + `VERCEL_TELEMETRY_DISABLED=1` (per-run) |
| Vercel Claude Code plugin | any | installed-plugin check + `VERCEL_PLUGIN_TELEMETRY=off` |
| Windsurf / Codeium (Devin Desktop) | any | manual account privacy / Data Controls |
| Zed | darwin, linux, win32 | settings.json telemetry controls |
| Ollama | any | manual server privacy guidance + optional cloud opt-out *(confirmation-gated — see warnings)* |
| macOS system privacy | darwin | `defaults write` (AdLib, CrashReporter) |
| Windows system privacy | win32 | `reg add` (Recall, Copilot, Telemetry, AdvertisingInfo) |
| Linux system privacy | linux | Flatpak OS-info header opt-out + manual GNOME/KDE controls |

### Warnings before you run it

- **Anthropic Claude Code users:** `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` and `DISABLE_TELEMETRY=1` do more than stop telemetry: they can also disable remote feature-flag delivery and hide features. The bisection recorded in [#142](https://github.com/lazyants/claude-plugins/issues/142) measured this against Claude Code **2.1.241 on 2026-08-24**; exact affected features depend on the installed build. Both edits require confirmation and show their trade-offs. Declining leaves the narrower bundle (`DISABLE_ERROR_REPORTING=1`, `DISABLE_FEEDBACK_COMMAND=1`, `CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1`, `skipWebFetchPreflight: true`). `DISABLE_FEEDBACK_COMMAND` deliberately disables `/feedback`; omit it to keep that command. Avoid using `DISABLE_GROWTHBOOK` as a privacy opt-out.
- **Cursor:** quit the app before the skill edits any JSON — Cursor's Electron process rewrites settings on graceful quit and will overwrite your changes.
- **VS Code and Antigravity:** quit the app before settings edits; a failed process check defers the edit. Editor telemetry and AI-training controls are separate; follow the vendor's current instructions for both.
- **Vercel CLI:** the subcommand `vercel telemetry disable` is persistent; `VERCEL_TELEMETRY_DISABLED=1` is per-run only and does **not** change the persisted status reported by `vercel telemetry status`. The skill applies both so you're covered either way.
- **Vercel Claude Code plugin (separate from the Vercel CLI):** opt out with `VERCEL_PLUGIN_TELEMETRY=off` in the environment that launches Claude Code. The [current upstream policy](https://github.com/vercel/vercel-plugin#telemetry) describes anonymous usage events and excludes prompt text and raw bash commands; older releases differed, as recorded in [#144](https://github.com/lazyants/claude-plugins/issues/144). The CLI opt-out does not cover the plugin.
- **Ollama:** cloud opt-outs also disable cloud models and web search. They require a separate confirmation; local inference and model downloads are different network surfaces.
- **OS limits:** Apple Intelligence restrictions keys require device management; use System Settings on unmanaged Macs. [Windows diagnostic data off](https://learn.microsoft.com/en-us/windows/privacy/configure-windows-diagnostic-data-in-your-organization#diagnostic-data-off) is available on Enterprise, Education and Server; Home and Pro retain the Required floor. Flatpak's `report-os-info` controls its OS-info request header, not applications' own telemetry; GNOME/KDE reporting controls depend on the installed desktop.

### What this does **not** cover

- Prompts and outputs still go to each vendor's API. To avoid that path you must switch providers (e.g. route Claude Code to AWS Bedrock / Google Vertex / Azure Foundry).
- Existing local state (Codex sqlite, conversation logs, OAuth tokens) is **reported**, never deleted.
- Cursor AI telemetry when Privacy Mode is off — the only vendor-blessed control is the UI toggle.

See [`DISCLAIMER.md`](./DISCLAIMER.md) for the full no-warranty statement.

## `db-guardrails` — v1.1.0

Stop AI coding agents from accidentally emptying your database. It exists because it happened — an agent ran `artisan migrate` with a test flag that did *not* isolate to the test database and wiped the development database. Twice. `db-guardrails` is the hardened, generalised result.

Trigger phrases: "harden the database", "protect the database", "db guardrails", "stop dropping the database", "database privilege separation", etc. — full list in `plugins/db-guardrails/skills/db-guardrails/SKILL.md`.

### What it does

- **Layer 4 — the hook (auto-on).** A `PreToolUse:Bash` hook blocks destructive database commands the moment the plugin is installed, in **any** project. Recognised across 15+ stacks: raw SQL (`DROP`, `TRUNCATE`, `DELETE` without `WHERE`), Laravel, Rails, Django, Prisma, TypeORM, Sequelize, Knex, Drizzle, Doctrine/Symfony, EF Core, Alembic, Flyway, Liquibase, MongoDB database/collection drops and empty-filter deletes, Redis, Docker volume deletion (including `docker system prune --volumes`), and recursive removal of DB data directories. Blocked attempts are logged to `~/.claude/logs/destructive-db-blocked.log`.
- **Layers 1–3 — the `/db-guardrails` skill.** Run it once per project. It detects the database engine and framework, then scaffolds privilege separation for MySQL/MariaDB, PostgreSQL and SQL Server, framework guards for Laravel/Django/Rails/Symfony, Node/EF Core connection patterns, and test isolation. Database privileges protect against schema deletion after effective grants and ownership are verified; runtime `DELETE` rights still permit deleting rows. MongoDB's `readWrite` role permits collection drops and mass deletes, so its custom-role guidance states the remaining limits.

### The bypass

The hook is bypassed only by starting Claude Code with `ALLOW_DESTRUCTIVE_DB_HOOK=true` in the shell — a deliberate, out-of-band human action. There is no inline comment or flag that re-enables a single command, by design: an LLM could append a sentinel to any command to bypass its own guard.

### Dependency

The hook uses standard system `awk` for command scanning and parses JSON with `jq` (preferred) or `python3` — at least one JSON parser must be on `PATH`. If a required tool is missing it returns a non-blocking hook error (exit 1), showing an inactive-guard warning in the transcript while allowing the command. Install the missing tool to restore protection.

### What the hook is not

It is a fast heuristic — instant, legible feedback that catches the *accidental* destructive command. It is not a hard guarantee; a command can be phrased past a regex, and it cannot stop a non-Claude actor. That is why layer 1 (database privilege separation) exists — run the skill.

## `obsidian-project-vault` — v1.1.0

Set up, migrate, audit, and operate an Obsidian vault as an **LLM Wiki** — a persistent, compounding knowledge base maintained by Claude Code instead of a one-shot RAG retrieval surface. Three-layer architecture (raw sources / wiki / schema), four setup modes (create, migrate, audit, ingest), and a query-and-file-back loop so every answer the LLM derives is folded back into the vault.

Trigger phrases: "set up obsidian", "migrate vault", "audit vault", "wiki-lint", "ingest sources", etc. — full list in `plugins/obsidian-project-vault/skills/obsidian-project-vault/SKILL.md`.

### What it covers

- **Setup modes** — create a fresh `vault/` subfolder, migrate an existing standalone vault into a project repo (per-file content diffs and verified preservation of project edits before deletion; unresolved pairs stay for human review), or audit a vault's structural health.
- **Wiki pattern** — three layers (raw sources, wiki, schema), Report template with frontmatter, INDEX.md navigation, CLAUDE.md workflow integration.
- **Ongoing operations** — ingest Markdown, PDFs, data files, and transcripts with adjacent source-state sidecars and legacy Markdown state support; query the vault and file findings back; lint Markdown links, wikilinks, connectivity, schema, freshness, and duplicate candidates; merge confirmed duplicate pages while preserving content, citations, and inbound links.
- **Git + `.obsidian/`** — `.gitignore` patterns, vault MCP config, sane defaults for human-side workflow (Web Clipper, Dataview, graph view).

## `cc-usage-coach` — v1.1.0

Personalized, behavior-aware analysis of where your Claude Code (Max / Pro) usage-limit tokens go, with ranked, low-effort ways to use fewer — computed entirely from your **local** session logs. Python measures; Claude concludes.

Trigger phrases: "where do my tokens go", "why am I hitting the usage limit", "usage coach", "analyze my Claude Code usage", "how to use fewer tokens", etc. — full list in `plugins/cc-usage-coach/skills/cc-usage-coach/SKILL.md`.

### What it does

- **Builds a path-free signal pack.** A skill reads your local session logs and runs `scripts/extract.py` (logs → local `dataset/`) then `scripts/signals.py` (dataset → `signal_pack.json` + a local-only `source_index.json`). The signal pack is an aggregate of your token shapes, cache patterns, tool mix, and session lengths — no paths, no prompt text.
- **Compares like sessions and reports observed errors.** Candidate baselines and percentile factors distinguish real sessions, subagents, and workflow logs. Error counts and rates describe retained dataset sessions; they do not estimate retry token cost. Custom tool names appear as opaque IDs, with local name resolution for your report.
- **Writes a personalized report.** The Claude runtime reads `signal_pack.json` and produces a plain-language breakdown of where your limit tokens go plus a ranked list of low-effort levers tailored to how you actually work — not generic advice.
- **Per-session arc.** `scripts/arc.py <source_ref>` inspects a single session's prompt arc (referenced by an opaque `source_ref`) so you can see how one conversation consumed budget over time. Local-only.

### Privacy

The **scripts** are local-first: they read local logs only and make no network calls of their own. `signal_pack.json` is path-free and safe to share; `source_index.json`, `project_index.json`, `tool_index.json`, the `dataset/`, and the `arc.py` digest are local-only — they hold real paths, project/tool names, and prompt text, are written `0600` where applicable, and must never be uploaded. Sessions are referred to only by an opaque `source_ref`, and custom tool names only by opaque IDs. The **report**, though, is written by the Claude Code model: the skill sends it the signal pack and (for sessions inspected via `arc.py` in step 4) raw prompt excerpts as prompt context — so on Max/Pro that data goes to Anthropic's API like any Claude Code conversation. Those excerpts are never added to the shareable pack, but the report step is not "nothing leaves your machine."

### Environment variables

- `CLAUDE_CONFIG_DIR` — honored; points the scan at a non-default config directory.
- `CC_COACH_CONFIG_DIRS` — comma-separated extra config dirs to scan (default scans only the standard `.claude`).
- `CC_COACH_OUT` — output location. Precedence: `$CC_COACH_OUT` if set, else next to the scripts if writable, else `${XDG_CACHE_HOME:-~/.cache}/cc-usage-coach/`.

## `enduser-handbook` — v1.18.3

Author, capture, and publish a Diátaxis-structured end-user handbook (tutorials, how-tos, reference, explanation) for any project. The methodology — pre-read mandate, anti-fabrication rules, capture safety, page identity, manifest discipline, glossary and tone consistency, completeness gate, "running UI is the primary source" — ships as a reusable skill. Project-specific bindings (language, register, stack globs, capture engine, publish target, glossary) live in `.claude/handbook/profile.yml` so the same skill produces a German shopkeeper-register handbook for one project and an English developer-register handbook for the next without forking the workflow.

Sibling to [`obsidian-project-vault`](#obsidian-project-vault--v110): where `obsidian-project-vault` builds the *internal* LLM Wiki that the team and Claude Code use, `enduser-handbook` builds the *external* end-user manual that ships to customers. They compose — `enduser-handbook`'s default publish-target adapter is `obsidian-vault`, so the handbook can be written straight into a vault scaffolded by `obsidian-project-vault` (separate folder, separate INDEX wiring, separate frontmatter shape). One vault, two audiences.

Trigger phrases: "write the end-user handbook", "update the user manual", "add a handbook chapter for <feature>", "re-capture handbook screenshots", etc. — full list in `plugins/enduser-handbook/skills/enduser-handbook/SKILL.md`.

### What it covers

- **Profile-driven** — language, register, stack/route globs, capture engine, publish target, glossary discipline all declared in `.claude/handbook/profile.yml`. The skill halts loudly if the profile is missing or unknown rather than guessing.
- **Running UI is the source** — code only tells the skill *which* features and routes exist; every described feature must be captured live, never fabricated from the codebase.
- **Diátaxis structure** — tutorials, how-tos, reference, and explanation each have their own discipline; chapters are gated on completeness before publish.
- **Publish-target adapters** — currently `obsidian-vault` and `static-md` (universal plain-Markdown) — paths, INDEX wiring, link syntax, frontmatter shape governed by the adapter, not improvised.
- **Month-over-month consistency** — mandatory pre-read of style guide + every reference file every session, so tone and terminology stay stable as the handbook grows.

### What it is **not**

- Developer / API / architecture docs — those belong in `CLAUDE.md`, `AGENTS.md`, or the project's internal knowledge area (e.g. an `obsidian-project-vault` wiki).
- A one-shot generator — it is a long-lived authoring loop maintained over the project's lifetime.

### Tips for best results

- **Plan first, then go wide.** In Claude Code, sketch the chapter plan before writing anything (plan mode), then drive authoring and review at high effort with multi-agent orchestration (e.g. `ultracode`) so several agents capture, cross-check, and validate coverage in parallel instead of one linear pass.
- **One page at a time.** Author and capture a single chapter per pass and keep its scope tight. A focused page is far easier to get right — and to verify — than a sprawling one; resist bundling unrelated features into one chapter.
- **Review from more than one perspective.** Have several agents read the drafted chapter, each from a different angle (a first-time user, a power user, a skeptic hunting for fabricated or undocumented behavior). More viewpoints beat one — no single pass catches everything.
- **Rerun and validate coverage.** When a chapter (or the whole handbook) is done, run the skill again as a completeness pass: walk the actual feature surface and confirm every feature is described. The first pass always misses some.

## `literary-translator` — v1.222.1

High-fidelity literary **book translation** over a Gutenberg-style EPUB source (or, in expert mode, a hand-co-designed `custom` extractor for any other source shape; a `plain_text` adapter is specified but not yet implemented, #62). A `codex-translate → deterministic false-green gate → codex-review → Claude-fix` loop runs to convergence per segment / per novella — never per book — with a frozen name/realia **canon**, a configurable **verse policy**, and **ledger-based resumability**. The canon freeze is one-way, so under `live` research mode a glossary batch's source citations are reviewed *before* it is allowed to merge, while its fragment is still rewritable. Once every in-scope segment converges, the drafts optionally assemble into an Obsidian glossary-wiki (keyed on the frozen canon) behind a deterministic render/diff acceptance gate.

Step 0 creates `profile.yml` from the shipped example when it is absent and, in that same run, prints a questionnaire naming every intake decision still unanswered — the language pair, the glossary, the footnote apparatus, the output shape and the verse policy — each with what that choice costs. Before 1.71.0 the creating run exited and the questions appeared only on a later one; from 1.71.0 the run that writes the sentinels is the run that asks about them, so none of them is filled in silently on the operator's behalf. The one exception is named by the creation message itself: with PyYAML or `jsonschema` missing, the dependency preflight in between prints the install instruction instead of the questionnaire.

Trigger phrases: "translate this book", "set up a literary translation pipeline", "new book translation project", "translate this EPUB/story collection from X to Y", "Gutenberg EPUB translation", "resume book translation" — full list in `plugins/literary-translator/skills/literary-translator/SKILL.md`.

### What it covers

- **Engine loop** — per segment: codex translates, a deterministic false-green gate (`validate_draft.py`) rejects placeholder / empty / policy-violating drafts, codex reviews, Claude applies fixes, looped until converged. A fix turn may also REFUSE the finding it was handed, and `refuse_finding.py` gives that refusal a durable record the next turn reads, so a considered refusal is distinguishable from an overlooked finding. The scripts surface candidates and enforce schemas; the accuracy / identity calls are codex's, never a script's. Where the cost of guessing wrong is a book — a unit out of fix rounds with findings outstanding, a sweep whose own output re-opens converged work, the choice between re-reviewing and re-translating — the skill requires the decision to be put to the operator with each route priced, rather than settled silently.
- **Frozen name/realia canon** — a 1:1 `source_form → canonical_target_form` dictionary (`canon.json`) with a validation gate (`canon_validate.py`) and an opt-in human-adjudication gate (`canon_adjudication_audit.py`) that turns duplicate / merge / missed-pair / unresolved-queue review requirements into a persisted, machine-checkable record. The whole researched canon is opt-out (`glossary.enabled: false`, 1.70.0) for a project that does not want it; an existing `canon.json` is never discarded. Under `live` research a batch's citation review now leaves an approval record on disk naming the batch, the attempt and the digest of the bytes the judge audited, and the merge enforces one record per merged fragment instead of trusting the recording agent's own sentence.
- **Verse policy** — configurable handling of verse vs prose (`rendered` / `literal_gloss` fields, per-mode validation). Since 1.70.0 the six-value `verse_policy.mode` is a mandatory intake question rather than a value the orchestrator picks from its own reading of the source: it decides what a review may fail a segment on, and it is hashed, so answering it before the first dispatch is free and changing it afterwards restales every already-converged segment in the volume.
- **Ledger-based resumability** — a `ledger.json` with a composite cache key so an interrupted run resumes safely and re-applies any style-bible / canon edit rather than shipping stale drafts. Since 1.71.0 a dispatch that would translate over a draft belonging to some *other* run — the shape a fresh `RUN_ID` produces for work still in flight — refuses and names each refused segment with its owner, instead of overwriting hand-applied fixes and reporting success. A pin checks every selected segment, as before; the unpinned path checks all of them except those classified `stale`, whose dispatch already requires an explicit `--allow-retranslate-converged`. `driver_status.py` is the read-only companion to all of this: a batch that runs for hours and prints its own JSON line only at the end can be asked how far along it is, without touching its state. It reports what the artifacts RECORD, with provenance, and deliberately concludes no process state — there is no running/finished/died field to mistake for one.
- **Source adapters** — `gutenberg_epub` (the one working built-in adapter) plus an expert-mode `custom` extractor (supported, experimental); `plain_text` is specified but not yet implemented (#62). Scripts are self-anchored and stdlib-first; each emits one JSON line to stdout, human detail to stderr, exit 0 / 1 / 2.
- **Worker usage accounting** — `worker_usage.py` reads explicitly selected Claude transcripts, Codex rollouts or exec JSONL, distinguishes cache input and observation units, and reports record hashes without emitting prompt text. The [lean-worker investigation](plugins/literary-translator/skills/literary-translator/references/lean-worker-investigation.md) records transport constraints and the blind A/B prerequisite for changing workers.
- **Book assembly + output rendering** — once every in-scope segment is converged, `assemble.py` joins the manifest + per-segment drafts + verse map (ledger-gated on `converged` + sha1 match) into a target-agnostic NodeStream, and an output adapter renders it. The shipped `obsidian` adapter produces a vault of chapter notes with folder-qualified `[[wikilinks]]`, footnotes, and verse blocks, plus one entity note per canon entry (by default, canon IS the entity registry). A book whose names cannot be seeded from a list in advance can instead declare, in `output.entity_markup` (1.74.0), the elements its translator marks inline as it goes: assembly strips those elements so no markup reaches the reader, and — under `index_from: markup` — the renderer builds notes from the marked spans, composing with canon rather than competing with it. `diff_rendered_output.py` is a deterministic render/diff acceptance gate (`--accept-baseline`, then re-render must match). All fail-closed against symlink data-loss.

### Status & scope

- Source-language extraction is proven against **Historiettes' 17th-century French specifically**, not French in general; every other language config — and every other French source — ships as an unverified **starter preset gated by a mandatory smoke test** (see `plugins/literary-translator/skills/literary-translator/references/language-pair-parameterization.md`).
- The shipped `gutenberg_epub` source adapter and the expert-mode `custom` extractor remain **experimental / unstable** until each is pilot-proven end-to-end on a real project (see `references/source-format-adapters/`); `plain_text` is specified but not yet implemented (#62).
- Scoped to texts whose natural segments / chapters fit under a configurable per-segment word cap; a novel with genuinely long natural chapters is out of scope for v1.
- The Workflow-orchestration reliability pass shipped in v1.2 is plugin hardening that has never been pilot-proven — a real end-to-end pilot run is still the honest gate before treating it as fully load-bearing; a synchronous codex block-and-hang on a DISPATCH call's own `await` remains an accepted residual risk (see `plugins/literary-translator/skills/literary-translator/references/gotchas.md`).

## `multi-profile-plugins` — v1.5.0

Understand and diagnose how Claude Code stores plugins when several `CLAUDE_CONFIG_DIR` profiles run on one machine. If some profiles share a `plugins/` directory (commonly via symlinks), a `claude plugin` op from one profile re-stamps the shared `known_marketplaces.json`, and every other profile then fails with `Marketplace 'X' has a corrupted installLocation …`; catalog-scoped garbage collection can also delete a plugin version one profile still uses. This plugin explains the mechanism and ships a read-only health check — it does **not** perform an automated migration.

The same shape recurs one CLI over: the Codex CLI selects its home with `CODEX_HOME`, which is how one machine runs several OpenAI accounts side by side. That isolation decides where files are read FROM but not what they SAY, so a profile seeded by copying the base `config.toml` inherits every absolute path in it and is sent straight back into the original home — including `CODEX_HOME` itself, pinned inside `[mcp_servers.*.env]`, which re-enters the base home from the MCP subprocess while the CLI that spawned it stays correctly isolated. A second skill covers that, with its own health check.

Trigger phrases: "corrupted installLocation", "claude plugin across profiles", "CLAUDE_CONFIG_DIR profiles", "plugin deleted in one profile", "multi-profile plugins", "CODEX_HOME", "second codex account", "codex profile reads the wrong home", "how much usage limit is left", "when does my limit reset", "codex reset vouchers", "claude reset vouchers" — full lists in `plugins/multi-profile-plugins/skills/multi-profile-plugins/SKILL.md`, `.../skills/multi-profile-codex/SKILL.md`, and `.../skills/code-limits/SKILL.md`.

### What it covers

- **The Claude Code skill** — when to keep profiles' plugin stores shared vs. independent, why a shared store causes "corrupted installLocation" churn and cross-profile deletion, and (conceptually) the structural fix: give each profile its own independent `plugins/` store. Diagnosis-and-reasoning only; no bundled destructive tool.
- **The Codex skill** (`skills/multi-profile-codex/`) — what `CODEX_HOME` does and does not isolate, why `--profile` is not a substitute (it layers a config file but shares one `auth.json`), which config blocks to drop when seeding a new home and which to keep, and why a per-profile launcher belongs on `PATH` as a shim rather than in a shell rc as a function.
- **The Codex health check** (`skills/multi-profile-codex/scripts/inspect_codex_profiles.py`) — read-only, stdlib-only; reports whether two homes share one `auth.json` **or merely the same account** (distinct files, one usage pool — the quiet failure), which dotted TOML key in a config points into another profile's home, and which content stores (`sessions/`, `plugins/`, `cache/`, …) resolve to a shared target. Matching is boundary-aware: a substring test false-positives every sibling (`~/.codex` is a substring of `~/.codex2`) and a `startswith` test then misses the real pins, which sit mid-string inside `:`-joined paths and JSON blobs.
- **The usage-limit skill** (`skills/code-limits/`) — one report covering every Claude Code profile and Codex home, as a single table -- one row per account, its five-hour and weekly figures side by side, ordered by account name so a profile's rows sit together and stay put between runs -- in colour when it is looking at a terminal. Claude Code is read live by default, falling back to its on-disk usage cache only when the live read comes back empty (`--live` makes the same call and never falls back); Codex is read live over `codex app-server`'s `account/rateLimits/read` JSON-RPC call. It opens with the "usage limit reset" vouchers of each Claude Code profile read live and each Codex home — how many each has, and the title and expiry of the one available there, since a voucher lapses whether or not anyone looks. It never redeems one. An install step puts the same report on `PATH` as `code-limit`, as a shim that runs the shipped script rather than a second copy of it.
- **The deep mechanism** (`references/cli-mechanism.md`) — the reverse-engineered `installLocation` prefix check (`path.resolve` + `startsWith`, no symlink resolution; cache-managed sources checked while `file`/`directory`/seed skip it), the catalog-scoped startup GC + `uninstall` that force full content independence rather than just a de-shared registry, the per-config-dir cache model, and the "a read-only probe can mutate state" caveat.
- **The health check** (`scripts/inspect_profiles.py`) — a read-only, stdlib-only Python script that auto-detects `~/.claude*` profiles (or takes explicit ones), reports which `plugins/` dirs are real vs. symlinked, flags profiles that share a `known_marketplaces.json` (the churn risk) **or a content store** (`cache/`/`data/`/`marketplaces/`/`.install-manifests/` — the cross-profile deletion vector a de-shared registry alone doesn't fix), and detects cross-profile pointer leaks via exact path-prefix matching (never a substring grep). Exits non-zero on any warning.

### Scope

Knowledge + read-only diagnostics, for both CLIs. Converting a shared store to independent per-profile stores touches live plugin data, and re-seeding a Codex home rewrites a config the desktop app also writes; both are intentionally left as deliberate, backed-up manual steps — not automated actions this plugin performs.

## `software-localizer` — v0.1.0

Translate a software project's user-interface strings into another language, or **audit the translation it already has**, with the discipline of `literary-translator`: decide the vocabulary once, translate in small checked units, review every value, and hand a person a report instead of a file to read end to end.

The plugin ships a fixed core; everything format-specific — how strings are collected from the project and how translations go back — is a **per-project adapter written when the skill is used**, preferably on the project's own tooling (a vue-i18n project's message compiler, PHP for Laravel lang files).

Trigger phrases: "localize this project", "translate the app into German", "check our Russian translation", "audit the translations", "add a language to this project", "resume the localization" — full procedure in the skill's `SKILL.md`.

### What it covers

- **Canon and glossary** — product terms, UI labels that other messages refer to, and do-not-translate names, proposed by a model turn (in audit mode with every current rendering side by side), approved by a person as a short core list, and frozen. Whether a translation uses an approved term, in any inflection, is the review's judgement, not a string match.
- **Two modes** — *translate* (new and changed strings only, incrementally) and *audit* (findings with proposed replacements; nothing is applied until a person accepts it).
- **Script checks on every export candidate and audit proposal** through the adapter's own parser — syntax, argument signatures, structure tokens, plural forms per the project's plural rules, surrounding whitespace, length, do-not-translate names.
- **Model turns with a fixed contract** — codex translates read-only and returns JSON; a Claude review gives a verdict for every id, bound to the hash of the exact value it judged.
- **A ledger that never overwrites a person** — existing translations start protected, a later human edit locks a message, and a changed source marks only the plugin's own translations stale.
- **Safe export** — re-collect before writing, refuse on any drift since review, write through a temporary copy that must differ only in the approved values, then replace files under a journal with backups that is rolled back on failure.
- **Adapter acceptance** — byte-exact unchanged round trip, an awkward-value round trip, parse sanity on every source, and a coverage turn against an independent inventory of the project.

### Status & scope

- **0.1.0.** The core and the adapter contract; no adapter or message syntax ships in the plugin. Not for extracting hard-coded strings out of code, right-to-left layout, screenshots, or translation-memory import.
- Requires the `codex` CLI for translation turns; review, audit, canon and coverage turns run as Claude subagents.

## License & disclaimer

MIT — see [`LICENSE`](./LICENSE). No warranty, no vendor affiliation — see [`DISCLAIMER.md`](./DISCLAIMER.md).
