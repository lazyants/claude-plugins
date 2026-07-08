# Lazy Ants — Claude Code plugins

Public plugins for [Claude Code](https://claude.com/claude-code), maintained under the `Lazy Ants` brand.

## Plugins

| Plugin | Version | What it does |
|---|---|---|
| [`ai-cli-optout`](#ai-cli-optout--v110) | 1.1.0 | Opt out of telemetry across every locally installed AI CLI / AI-enabled IDE, plus Vercel CLI and macOS / Windows OS-level privacy surfaces. |
| [`db-guardrails`](#db-guardrails--v100) | 1.0.0 | Stop AI coding agents from accidentally emptying your database — an always-on hook that blocks destructive DB commands across 15+ frameworks, plus a stack-aware installer for deeper safety layers. |
| [`obsidian-project-vault`](#obsidian-project-vault--v100) | 1.0.0 | Set up, migrate, audit, and operate an Obsidian vault as an LLM Wiki — a persistent, compounding knowledge base maintained by Claude Code. |
| [`cc-usage-coach`](#cc-usage-coach--v100) | 1.0.0 | Personalized, behavior-aware analysis of where your Claude Code (Max/Pro) usage-limit tokens go, with ranked, low-effort ways to use fewer — computed entirely from your local session logs. Python measures; Claude concludes. |
| [`enduser-handbook`](#enduser-handbook--v111) | 1.1.1 | Author, capture, and publish a Diátaxis-structured end-user handbook for any project — methodology shipped as a reusable skill, project-specific bindings supplied via `.claude/handbook/profile.yml`. |
| [`literary-translator`](#literary-translator--v100) | 1.0.0 | High-fidelity literary book translation over a Gutenberg-style EPUB or plain-text source — a codex-translate → deterministic false-green gate → codex-review → Claude-fix loop run to convergence, with a frozen name/realia canon, a configurable verse policy, and ledger-based resumability. |

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

## `ai-cli-optout` — v1.1.0

Opts out of telemetry, error reporting, analytics, feedback surveys, and related data collection across every locally installed AI CLI and AI-enabled IDE, plus Vercel CLI (adjacent developer tooling) and macOS / Windows OS-level privacy surfaces. One skill, thirteen vendors, data-driven. 369 test assertions guard vendor-schema invariants and script behavior.

Trigger phrases: "disable telemetry", "opt out of telemetry", "privacy mode", etc. — full list in `plugins/ai-cli-optout/skills/ai-cli-optout/SKILL.md`.

### Vendors covered (baseline 2026-04-24)

| Vendor | Platform | Kind |
|---|---|---|
| Anthropic Claude Code | any | settings.json + env *(2 edits confirmation-gated — see warnings)* |
| OpenAI Codex CLI | any | `~/.codex/config.toml` |
| Google Gemini CLI | any | settings.json + env |
| GitHub Copilot CLI + `gh` | any | `gh config set` + env |
| Cursor | darwin | manual only — Cmd+Shift+J → Privacy Mode |
| Cursor CLI (`cursor-agent`) | any | manual only — account-level Privacy Mode |
| Google Antigravity | darwin | settings.json (AI-training opt-out is email-only) |
| VS Code | darwin | settings.json (Copilot extension does not inherit) |
| PhpStorm | darwin | manual only — Settings → Tools → Usage Statistics |
| Vercel CLI | any | `vercel telemetry disable` (persistent) + `VERCEL_TELEMETRY_DISABLED=1` (per-run) |
| Vercel Claude Code plugin | any | env — `VERCEL_PLUGIN_TELEMETRY=off` (interim, not yet first-class) |
| macOS system privacy | darwin | `defaults write` (AdLib, CrashReporter) |
| Windows system privacy | win32 | `reg add` (Recall, Copilot, Telemetry, AdvertisingInfo) |

### Warnings before you run it

- **Anthropic Claude Code users:** setting `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` or `DISABLE_TELEMETRY=1` does more than stop telemetry — both flags also disable the `/remote-control` command and, on Max / Team / Enterprise plans, silently switch Opus 4.6 off the 1M-context default model. See [anthropics/claude-code#34178](https://github.com/anthropics/claude-code/issues/34178) and `KNOWN_ISSUES.md` §C2. The skill gates both edits behind `requires_confirmation: true` so you see the trade-off and can decline. A safe narrow opt-out that leaves these features intact (`DISABLE_ERROR_REPORTING=1`, `DISABLE_FEEDBACK_COMMAND=1`, `CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1`, `skipWebFetchPreflight: true`) is documented in `KNOWN_ISSUES.md` §C2.
- **Cursor:** quit the app before the skill edits any JSON — Cursor's Electron process rewrites settings on graceful quit and will overwrite your changes.
- **Antigravity:** the AI-training opt-out is **email-only** to `antigravity-support@google.com`; no CLI / setting exists. The skill surfaces this in its report but cannot automate it.
- **Vercel CLI:** the subcommand `vercel telemetry disable` is persistent; `VERCEL_TELEMETRY_DISABLED=1` is per-run only and does **not** change the persisted status reported by `vercel telemetry status`. The skill applies both so you're covered either way.
- **Vercel Claude Code plugin (separate from the Vercel CLI):** if installed, it sends every bash command string to `telemetry.vercel.com` by default. Interim opt-out: `VERCEL_PLUGIN_TELEMETRY=off`. Not yet a first-class vendor in this skill — tracked in `KNOWN_ISSUES.md`.

### What this does **not** cover

- Prompts and outputs still go to each vendor's API. To avoid that path you must switch providers (e.g. route Claude Code to AWS Bedrock / Google Vertex / Azure Foundry).
- Existing local state (Codex sqlite, conversation logs, OAuth tokens) is **reported**, never deleted.
- Cursor AI telemetry when Privacy Mode is off — the only vendor-blessed control is the UI toggle.

See [`DISCLAIMER.md`](./DISCLAIMER.md) for the full no-warranty statement.

## `db-guardrails` — v1.0.0

Stop AI coding agents from accidentally emptying your database. It exists because it happened — an agent ran `artisan migrate` with a test flag that did *not* isolate to the test database and wiped the development database. Twice. `db-guardrails` is the hardened, generalised result.

Trigger phrases: "harden the database", "protect the database", "db guardrails", "stop dropping the database", "database privilege separation", etc. — full list in `plugins/db-guardrails/skills/db-guardrails/SKILL.md`.

### What it does

- **Layer 4 — the hook (auto-on).** A `PreToolUse:Bash` hook blocks destructive database commands the moment the plugin is installed, in **any** project. Recognised across 15+ stacks: raw SQL (`DROP`, `TRUNCATE`, `DELETE` without `WHERE`), Laravel, Rails, Django, Prisma, TypeORM, Sequelize, Knex, Drizzle, Doctrine/Symfony, EF Core, Alembic, Flyway, Liquibase, MongoDB, Redis, plus `docker compose down -v` and `rm -rf` of DB data directories. Blocked attempts are logged to `~/.claude/logs/destructive-db-blocked.log`.
- **Layers 1–3 — the `/db-guardrails` skill.** Run it once per project. It detects the database engine and framework, then scaffolds database-level privilege separation (the app role loses `DROP` — works for MySQL/MariaDB and PostgreSQL), a framework boot guard, and test-environment isolation.

### The bypass

The hook is bypassed only by starting Claude Code with `ALLOW_DESTRUCTIVE_DB_HOOK=true` in the shell — a deliberate, out-of-band human action. There is no inline comment or flag that re-enables a single command, by design: an LLM could append a sentinel to any command to bypass its own guard.

### Dependency

The hook parses its input with `jq` (preferred) or `python3` — at least one must be on `PATH`. If neither is found the hook warns and allows rather than breaking every Bash command, so install `jq`.

### What the hook is not

It is a fast heuristic — instant, legible feedback that catches the *accidental* destructive command. It is not a hard guarantee; a command can be phrased past a regex, and it cannot stop a non-Claude actor. That is why layer 1 (database privilege separation) exists — run the skill.

## `obsidian-project-vault` — v1.0.0

Set up, migrate, audit, and operate an Obsidian vault as an **LLM Wiki** — a persistent, compounding knowledge base maintained by Claude Code instead of a one-shot RAG retrieval surface. Three-layer architecture (raw sources / wiki / schema), four setup modes (create, migrate, audit, ingest), and a query-and-file-back loop so every answer the LLM derives is folded back into the vault.

Trigger phrases: "set up obsidian", "migrate vault", "audit vault", "wiki-lint", "ingest sources", etc. — full list in `plugins/obsidian-project-vault/skills/obsidian-project-vault/SKILL.md`.

### What it covers

- **Setup modes** — create a fresh `vault/` subfolder, migrate an existing standalone vault into a project repo (with diff-before-delete safety), or audit a vault's structural health.
- **Wiki pattern** — three layers (raw sources, wiki, schema), Report template with frontmatter, INDEX.md navigation, CLAUDE.md workflow integration.
- **Ongoing operations** — ingest new sources, query the vault and file findings back, lint vault health, prune stale entries.
- **Git + `.obsidian/`** — `.gitignore` patterns, vault MCP config, sane defaults for human-side workflow (Web Clipper, Dataview, graph view).

## `cc-usage-coach` — v1.0.0

Personalized, behavior-aware analysis of where your Claude Code (Max / Pro) usage-limit tokens go, with ranked, low-effort ways to use fewer — computed entirely from your **local** session logs. Python measures; Claude concludes.

Trigger phrases: "where do my tokens go", "why am I hitting the usage limit", "usage coach", "analyze my Claude Code usage", "how to use fewer tokens", etc. — full list in `plugins/cc-usage-coach/skills/cc-usage-coach/SKILL.md`.

### What it does

- **Builds a path-free signal pack.** A skill reads your local session logs and runs `scripts/extract.py` (logs → local `dataset/`) then `scripts/signals.py` (dataset → `signal_pack.json` + a local-only `source_index.json`). The signal pack is an aggregate of your token shapes, cache patterns, tool mix, and session lengths — no paths, no prompt text.
- **Writes a personalized report.** The Claude runtime reads `signal_pack.json` and produces a plain-language breakdown of where your limit tokens go plus a ranked list of low-effort levers tailored to how you actually work — not generic advice.
- **Per-session arc.** `scripts/arc.py <source_ref>` inspects a single session's prompt arc (referenced by an opaque `source_ref`) so you can see how one conversation consumed budget over time. Local-only.

### Privacy

The **scripts** are local-first: they read local logs only and make no network calls of their own. `signal_pack.json` is path-free and safe to share; `source_index.json`, `project_index.json`, the `dataset/`, and the `arc.py` digest are local-only — they hold real paths, project names, and prompt text, are written `0600` where applicable, and must never be uploaded. Sessions are referred to only by an opaque `source_ref`. The **report**, though, is written by the Claude Code model: the skill sends it the signal pack and (for sessions inspected via `arc.py` in step 4) raw prompt excerpts as prompt context — so on Max/Pro that data goes to Anthropic's API like any Claude Code conversation. Those excerpts are never added to the shareable pack, but the report step is not "nothing leaves your machine."

### Environment variables

- `CLAUDE_CONFIG_DIR` — honored; points the scan at a non-default config directory.
- `CC_COACH_CONFIG_DIRS` — comma-separated extra config dirs to scan (default scans only the standard `.claude`).
- `CC_COACH_OUT` — output location. Precedence: `$CC_COACH_OUT` if set, else next to the scripts if writable, else `${XDG_CACHE_HOME:-~/.cache}/cc-usage-coach/`.

## `enduser-handbook` — v1.1.1

Author, capture, and publish a Diátaxis-structured end-user handbook (tutorials, how-tos, reference, explanation) for any project. The methodology — pre-read mandate, anti-fabrication rules, capture safety, page identity, manifest discipline, glossary and tone consistency, completeness gate, "running UI is the primary source" — ships as a reusable skill. Project-specific bindings (language, register, stack globs, capture engine, publish target, glossary) live in `.claude/handbook/profile.yml` so the same skill produces a German shopkeeper-register handbook for one project and an English developer-register handbook for the next without forking the workflow.

Sibling to [`obsidian-project-vault`](#obsidian-project-vault--v100): where `obsidian-project-vault` builds the *internal* LLM Wiki that the team and Claude Code use, `enduser-handbook` builds the *external* end-user manual that ships to customers. They compose — `enduser-handbook`'s default publish-target adapter is `obsidian-vault`, so the handbook can be written straight into a vault scaffolded by `obsidian-project-vault` (separate folder, separate INDEX wiring, separate frontmatter shape). One vault, two audiences.

Trigger phrases: "write the end-user handbook", "update the user manual", "add a handbook chapter for <feature>", "re-capture handbook screenshots", etc. — full list in `plugins/enduser-handbook/skills/enduser-handbook/SKILL.md`.

### What it covers

- **Profile-driven** — language, register, stack/route globs, capture engine, publish target, glossary discipline all declared in `.claude/handbook/profile.yml`. The skill halts loudly if the profile is missing or unknown rather than guessing.
- **Running UI is the source** — code only tells the skill *which* features and routes exist; every described feature must be captured live, never fabricated from the codebase.
- **Diátaxis structure** — tutorials, how-tos, reference, and explanation each have their own discipline; chapters are gated on completeness before publish.
- **Publish-target adapters** — currently `obsidian-vault` and `static-md` (universal plain-Markdown) — paths, INDEX wiring, link syntax, frontmatter shape governed by the adapter, not improvised.
- **Month-over-month consistency** — mandatory pre-read of style guide + every reference file every session, so tone and terminology stay stable as the handbook grows.
- **Reference capture tooling (v1.0.5)** — ships non-normative Playwright reference implementations for the parts that carry the most risk: a live-DOM surface enumerator (every control's verbatim text/title/aria-label/href/role, icon-only controls included) and a context-level capture guard (fail-closed request classifier, service-worker block, WebSocket/beacon/SSE handling, safe Escape-first dialog dismiss, reproducible PII mask + leak-assert). The methodology stays engine-agnostic — fork the assets for other engines.
- **Revalidation / audit mode (v1.0.5)** — a first-class path for re-validating an already-merged chapter: re-derive the surface from the running UI, diff against the existing chapter and manifest, and classify each delta (no-op / accepted-diff / material) — material deltas still emit a delta manifest and halt for review.
- **Surface / guard / capture hardening (v1.0.6)** — the surface enumerator now also catches framework glyph/icon controls (`.btn`, `[data-bs-toggle]`, `[data-toggle]`) and records each control's `class` to drive destructive-action hints; the capture guard adds a `'benign'` verdict so blocked dev-telemetry (e.g. laravel-boost, Sentry) no longer false-trips the safety assertion; `captureRegion` gains a `maxHeight` cap for runaway-height modals; and the completeness gate ships a concrete disclose trigger-list + prose templates.
- **Capture-safety correctness (v1.1.1)** — a broken `<img>`'s `alt` text *is* painted into the screenshot (browser replacement-rendering), so it is reclassified from a "non-rendered attribute" to a painted-but-unscannable surface the eyeball backstop must catch — only `title`/`aria-label` are genuinely non-rendered; and the mask/leak-scan scope rule is made explicitly bidirectional — scope must equal the captured frame, so a full-viewport shot scopes the scan to the document root (an element- or region-scoped shot to its own node) and framed app chrome (e.g. a header user name) is never left unscanned.
- **Capture-determinism guardrails (v1.1.1)** — `page-identity.md` now warns against four author-time traps that ship a wrong or broken shot while the run still looks green: asserting visibility on a layout wrapper that collapses to zero height (assert a content-bearing child, not a bare container); capturing a mid-animation frame (settle transitions / disable animations first); a full-element shot of lazy-loaded or virtualized content shipping blank below-the-fold rows (scroll to load first); and a deliberately staged data-state silently reverting (pair every precondition with a fail-closed assertion that it held).

### What it is **not**

- Developer / API / architecture docs — those belong in `CLAUDE.md`, `AGENTS.md`, or the project's internal knowledge area (e.g. an `obsidian-project-vault` wiki).
- A one-shot generator — it is a long-lived authoring loop maintained over the project's lifetime.

### Tips for best results

- **Plan first, then go wide.** In Claude Code, sketch the chapter plan before writing anything (plan mode), then drive authoring and review at high effort with multi-agent orchestration (e.g. `ultracode`) so several agents capture, cross-check, and validate coverage in parallel instead of one linear pass.
- **One page at a time.** Author and capture a single chapter per pass and keep its scope tight. A focused page is far easier to get right — and to verify — than a sprawling one; resist bundling unrelated features into one chapter.
- **Review from more than one perspective.** Have several agents read the drafted chapter, each from a different angle (a first-time user, a power user, a skeptic hunting for fabricated or undocumented behavior). More viewpoints beat one — no single pass catches everything.
- **Rerun and validate coverage.** When a chapter (or the whole handbook) is done, run the skill again as a completeness pass: walk the actual feature surface and confirm every feature is described. The first pass always misses some.

## `literary-translator` — v1.0.0

High-fidelity literary **book translation** over a Gutenberg-style EPUB or plain-text source (or, in expert mode, a hand-co-designed custom extractor for any other source shape): a `codex-translate → deterministic false-green gate → codex-review → Claude-fix` loop, run to convergence per segment, with a frozen name/realia **canon**, a configurable **verse policy**, and **ledger-based resumability**. The loop runs per segment / per novella, never per book. v1 delivers converged per-segment drafts plus a full audit trail — not an assembled book (see non-goals).

Trigger phrases: "translate this book", "set up a literary translation pipeline", "new book translation project", "translate this EPUB/story collection from X to Y", "Gutenberg EPUB translation", "resume book translation" — full list in `plugins/literary-translator/skills/literary-translator/SKILL.md`.

### What it covers

- **Engine loop** — per segment: codex translates, a deterministic false-green gate (`validate_draft.py`) rejects placeholder / empty / policy-violating drafts, codex reviews, Claude applies fixes, looped until converged. The scripts surface candidates and enforce schemas; the accuracy / identity calls are codex's, never a script's.
- **Frozen name/realia canon** — a 1:1 `source_form → canonical_target_form` dictionary (`canon.json`) with a validation gate (`canon_validate.py`) and an opt-in human-adjudication gate (`canon_adjudication_audit.py`) that turns duplicate / merge / missed-pair / unresolved-queue review requirements into a persisted, machine-checkable record.
- **Verse policy** — configurable handling of verse vs prose (`rendered` / `literal_gloss` fields, per-mode validation).
- **Ledger-based resumability** — a `ledger.json` with a composite cache key so an interrupted run resumes safely and re-applies any style-bible / canon edit rather than shipping stale drafts.
- **Source adapters** — `gutenberg_epub`, `plain_text`, and an expert-mode `custom` extractor. Scripts are self-anchored and stdlib-first; each emits one JSON line to stdout, human detail to stderr, exit 0 / 1 / 2.

### Status & scope

- Source-language extraction is proven against **Historiettes' 17th-century French specifically**, not French in general; every other language config — and every other French source — ships as an unverified **starter preset gated by a mandatory smoke test** (see `plugins/literary-translator/skills/literary-translator/references/language-pair-parameterization.md`).
- One of the two shipped source adapters and the expert-mode custom extractor remain **experimental / unstable** until each is pilot-proven end-to-end on a real project (see `references/source-format-adapters/`).
- Scoped to texts whose natural segments / chapters fit under a configurable per-segment word cap; a novel with genuinely long natural chapters is out of scope for v1.

## License & disclaimer

MIT — see [`LICENSE`](./LICENSE). No warranty, no vendor affiliation — see [`DISCLAIMER.md`](./DISCLAIMER.md).
