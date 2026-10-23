# Plugin guide

Detailed setup, coverage, and limitations for the [Lazy Ants plugins](../README.md).
For installation and current versions, use the main README.

## `ai-cli-optout`

Applies documented opt-outs for telemetry, error reporting, analytics, feedback surveys and related
data collection across installed AI CLIs and IDEs, Vercel CLI and its Claude Code plugin, and macOS
/ Windows / Linux privacy controls. One skill, seventeen vendor entries, data-driven. Automated
checks guard vendor schemas, platform paths, installation markers and helper behavior. Notes and
platform limits are surfaced before changes; settings edits wait until the app is stopped, and Codex
analytics covers existing profile overlays as well as the main config.

Trigger phrases: "disable telemetry", "opt out of telemetry", "privacy mode", etc. — full list in
`plugins/ai-cli-optout/skills/ai-cli-optout/SKILL.md`.

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

- **Anthropic Claude Code users:** `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` and
  `DISABLE_TELEMETRY=1` do more than stop telemetry: they can also disable remote feature-flag
  delivery and hide features. The bisection recorded in
  [#142](https://github.com/lazyants/claude-plugins/issues/142) measured this against Claude Code
  **2.1.241 on 2026-08-24**; exact affected features depend on the installed build. Both edits
  require confirmation and show their trade-offs. Declining leaves the narrower bundle
  (`DISABLE_ERROR_REPORTING=1`, `DISABLE_FEEDBACK_COMMAND=1`,
  `CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1`, `skipWebFetchPreflight: true`).
  `DISABLE_FEEDBACK_COMMAND` deliberately disables `/feedback`; omit it to keep that command. Avoid
  using `DISABLE_GROWTHBOOK` as a privacy opt-out.
- **Cursor:** quit the app before the skill edits any JSON — Cursor's Electron process rewrites
  settings on graceful quit and will overwrite your changes.
- **VS Code and Antigravity:** quit the app before settings edits; a failed process check defers the
  edit. Editor telemetry and AI-training controls are separate; follow the vendor's current
  instructions for both.
- **Vercel CLI:** the subcommand `vercel telemetry disable` is persistent;
  `VERCEL_TELEMETRY_DISABLED=1` is per-run only and does **not** change the persisted status
  reported by `vercel telemetry status`. The skill applies both so you're covered either way.
- **Vercel Claude Code plugin (separate from the Vercel CLI):** opt out with
  `VERCEL_PLUGIN_TELEMETRY=off` in the environment that launches Claude Code. The [current upstream
  policy](https://github.com/vercel/vercel-plugin#telemetry) describes anonymous usage events and
  excludes prompt text and raw bash commands; older releases differed, as recorded in
  [#144](https://github.com/lazyants/claude-plugins/issues/144). The CLI opt-out does not cover the
  plugin.
- **Ollama:** cloud opt-outs also disable cloud models and web search. They require a separate
  confirmation; local inference and model downloads are different network surfaces.
- **OS limits:** Apple Intelligence restrictions keys require device management; use System Settings
  on unmanaged Macs. [Windows diagnostic data
  off](https://learn.microsoft.com/en-us/windows/privacy/configure-windows-diagnostic-data-in-your-organization#diagnostic-data-off)
  is available on Enterprise, Education and Server; Home and Pro retain the Required floor.
  Flatpak's `report-os-info` controls its OS-info request header, not applications' own telemetry;
  GNOME/KDE reporting controls depend on the installed desktop.

### What this does **not** cover

- Prompts and outputs still go to each vendor's API. To avoid that path you must switch providers
  (e.g. route Claude Code to AWS Bedrock / Google Vertex / Azure Foundry).
- Existing local state (Codex sqlite, conversation logs, OAuth tokens) is **reported**, never
  deleted.
- Cursor AI telemetry when Privacy Mode is off — the only vendor-blessed control is the UI toggle.

See [`DISCLAIMER.md`](../DISCLAIMER.md) for the full no-warranty statement.

## `obsidian-project-vault`

Set up, migrate, audit, and operate an Obsidian vault as an **LLM Wiki** — a persistent, compounding
knowledge base maintained by Claude Code instead of a one-shot RAG retrieval surface. Three-layer
architecture (raw sources / wiki / schema), four setup modes (create, migrate, audit, ingest), and a
query-and-file-back loop so every answer the LLM derives is folded back into the vault.

Trigger phrases: "set up obsidian", "migrate vault", "audit vault", "wiki-lint", "ingest sources",
etc. — full list in `plugins/obsidian-project-vault/skills/obsidian-project-vault/SKILL.md`.

### Vault operations

- **Setup modes** — create a fresh `vault/` subfolder, migrate an existing standalone vault into a
  project repo (per-file content diffs and verified preservation of project edits before deletion;
  unresolved pairs stay for human review), or audit a vault's structural health.
- **Wiki pattern** — three layers (raw sources, wiki, schema), Report template with frontmatter,
  INDEX.md navigation, CLAUDE.md workflow integration.
- **Ongoing operations** — ingest Markdown, PDFs, data files, and transcripts with adjacent
  source-state sidecars and legacy Markdown state support; query the vault and file findings back;
  lint Markdown links, wikilinks, connectivity, schema, freshness, and duplicate candidates; merge
  confirmed duplicate pages while preserving content, citations, and inbound links.
- **Git + `.obsidian/`** — `.gitignore` patterns, vault MCP config, sane defaults for human-side
  workflow (Web Clipper, Dataview, graph view).

## `enduser-handbook`

Author, capture, and publish a Diátaxis-structured end-user handbook (tutorials, how-tos, reference,
explanation) for any project. The methodology — pre-read mandate, anti-fabrication rules, capture
safety, page identity, manifest discipline, glossary and tone consistency, completeness gate,
"running UI is the primary source" — ships as a reusable skill. Project-specific bindings (language,
register, stack globs, capture engine, publish target, glossary) live in
`.claude/handbook/profile.yml` so the same skill produces a German shopkeeper-register handbook for
one project and an English developer-register handbook for the next without forking the workflow.

Sibling to [`obsidian-project-vault`](#obsidian-project-vault): where `obsidian-project-vault`
builds the *internal* LLM Wiki that the team and Claude Code use, `enduser-handbook` builds the
*external* end-user manual that ships to customers. They compose — `enduser-handbook`'s default
publish-target adapter is `obsidian-vault`, so the handbook can be written straight into a vault
scaffolded by `obsidian-project-vault` (separate folder, separate INDEX wiring, separate frontmatter
shape). One vault, two audiences.

Trigger phrases: "write the end-user handbook", "update the user manual", "add a handbook chapter
for <feature>", "re-capture handbook screenshots", etc. — full list in
`plugins/enduser-handbook/skills/enduser-handbook/SKILL.md`.

### Handbook workflow

- **Profile-driven** — language, register, stack/route globs, capture engine, publish target,
  glossary discipline all declared in `.claude/handbook/profile.yml`. The skill halts loudly if the
  profile is missing or unknown rather than guessing.
- **Running UI is the source** — code only tells the skill *which* features and routes exist; every
  described feature must be captured live, never fabricated from the codebase.
- **Diátaxis structure** — tutorials, how-tos, reference, and explanation each have their own
  discipline; chapters are gated on completeness before publish.
- **Publish-target adapters** — currently `obsidian-vault` and `static-md` (universal
  plain-Markdown) — paths, INDEX wiring, link syntax, frontmatter shape governed by the adapter, not
  improvised.
- **Month-over-month consistency** — mandatory pre-read of style guide + every reference file every
  session, so tone and terminology stay stable as the handbook grows.
- **Field-tested traps** — two reference pages collect pitfalls found in real use: one for capture
  specs (masks, request guards, waits, selectors, screenshot geometry, fixtures, the runner) and one
  for chapter authoring (behavioural claims, coverage, permission wording, parallel chapters,
  glossary and navigation wiring, static-site publishing).

### What it is **not**

- Developer / API / architecture docs — those belong in `CLAUDE.md`, `AGENTS.md`, or the project's
  internal knowledge area (e.g. an `obsidian-project-vault` wiki).
- A one-shot generator — it is a long-lived authoring loop maintained over the project's lifetime.

### Tips for best results

- **Plan first, then go wide.** In Claude Code, sketch the chapter plan before writing anything
  (plan mode), then drive authoring and review at high effort with multi-agent orchestration (e.g.
  `ultracode`) so several agents capture, cross-check, and validate coverage in parallel instead of
  one linear pass.
- **One page at a time.** Author and capture a single chapter per pass and keep its scope tight. A
  focused page is far easier to get right — and to verify — than a sprawling one; resist bundling
  unrelated features into one chapter.
- **Review from more than one perspective.** Have several agents read the drafted chapter, each from
  a different angle (a first-time user, a power user, a skeptic hunting for fabricated or
  undocumented behavior). More viewpoints beat one — no single pass catches everything.
- **Rerun and validate coverage.** When a chapter (or the whole handbook) is done, run the skill
  again as a completeness pass: walk the actual feature surface and confirm every feature is
  described. The first pass always misses some.

## `literary-translator`

High-fidelity literary **book translation** over a Gutenberg-style EPUB source (or, in expert mode,
a hand-co-designed `custom` extractor for any other source shape; a `plain_text` adapter is
specified but not yet implemented, #62). A `codex-translate → deterministic false-green gate →
codex-review → Claude-fix` loop runs to convergence per segment / per novella — never per book —
with a frozen name/realia **canon**, a configurable **verse policy**, and **ledger-based
resumability**. The canon freeze is one-way, so under `live` research mode a glossary batch's source
citations are reviewed *before* it is allowed to merge, while its fragment is still rewritable. Once
every in-scope segment converges, the drafts optionally assemble into an Obsidian glossary-wiki
(keyed on the frozen canon) behind a deterministic render/diff acceptance gate.

Step 0 creates `profile.yml` when it is absent and asks about the language pair, glossary,
footnotes, output, and verse policy before dispatch. Missing PyYAML or `jsonschema` must be
installed before the questionnaire can run.

Trigger phrases: "translate this book", "set up a literary translation pipeline", "new book
translation project", "translate this EPUB/story collection from X to Y", "Gutenberg EPUB
translation", "resume book translation" — full list in
`plugins/literary-translator/skills/literary-translator/SKILL.md`.

### Translation workflow

- **Engine loop** — per segment: codex translates, a deterministic false-green gate
  (`validate_draft.py`) rejects placeholder / empty / policy-violating drafts, codex reviews, Claude
  applies fixes, looped until converged. A fix turn may also REFUSE the finding it was handed, and
  `refuse_finding.py` gives that refusal a durable record the next turn reads, so a considered
  refusal is distinguishable from an overlooked finding. The scripts surface candidates and enforce
  schemas; the accuracy / identity calls are codex's, never a script's. Where the cost of guessing
  wrong is a book — a unit out of fix rounds with findings outstanding, a sweep whose own output
  re-opens converged work, the choice between re-reviewing and re-translating — the skill requires
  the decision to be put to the operator with each route priced, rather than settled silently.
- **Frozen name/realia canon** — a 1:1 `source_form → canonical_target_form` dictionary
  (`canon.json`) with a validation gate (`canon_validate.py`) and an opt-in human-adjudication gate
  (`canon_adjudication_audit.py`) that turns duplicate / merge / missed-pair / unresolved-queue
  review requirements into a persisted, machine-checkable record. The researched canon is optional
  (`glossary.enabled: false`) for a project that does not want it; an existing `canon.json` is never
  discarded. Under `live` research a batch's citation review now leaves an approval record on disk
  naming the batch, the attempt and the digest of the bytes the judge audited, and the merge
  enforces one record per merged fragment instead of trusting the recording agent's own sentence.
- **Verse policy** — configurable handling of verse vs prose (`rendered` / `literal_gloss` fields,
  per-mode validation). The six-value `verse_policy.mode` is a mandatory intake question rather than
  a value the orchestrator picks from its own reading of the source: it decides what a review may
  fail a segment on, and it is hashed, so answering it before the first dispatch is free and
  changing it afterwards restales every already-converged segment in the volume.
- **Ledger-based resumability** — a `ledger.json` with a composite cache key so an interrupted run
  resumes safely and re-applies any style-bible / canon edit rather than shipping stale drafts. A
  dispatch that would translate over a draft belonging to some *other* run — the shape a fresh
  `RUN_ID` produces for work still in flight — refuses and names each refused segment with its
  owner, instead of overwriting hand-applied fixes and reporting success. A pin checks every
  selected segment, as before; the unpinned path checks all of them except those classified `stale`,
  whose dispatch already requires an explicit `--allow-retranslate-converged`. `driver_status.py` is
  the read-only companion to all of this: a batch that runs for hours and prints its own JSON line
  only at the end can be asked how far along it is, without touching its state. It reports what the
  artifacts RECORD, with provenance, and deliberately concludes no process state — there is no
  running/finished/died field to mistake for one.
- **Source adapters** — `gutenberg_epub` (the one working built-in adapter) plus an expert-mode
  `custom` extractor (supported, experimental); `plain_text` is specified but not yet implemented
  (#62). Scripts are self-anchored and stdlib-first; each emits one JSON line to stdout, human
  detail to stderr, exit 0 / 1 / 2.
- **Worker usage accounting** — `worker_usage.py` reads explicitly selected Claude transcripts,
  Codex rollouts or exec JSONL, distinguishes cache input and observation units, and reports record
  hashes without emitting prompt text. The [lean-worker
  investigation](../plugins/literary-translator/skills/literary-translator/references/lean-worker-investigation.md)
  records transport constraints and the blind A/B prerequisite for changing workers.
- **Book assembly + output rendering** — once every in-scope segment is converged, `assemble.py`
  joins the manifest + per-segment drafts + verse map (ledger-gated on `converged` + sha1 match)
  into a target-agnostic NodeStream, and an output adapter renders it. The shipped `obsidian`
  adapter produces a vault of chapter notes with folder-qualified `[[wikilinks]]`, footnotes, and
  verse blocks, plus one entity note per canon entry (by default, canon IS the entity registry). A
  book whose names cannot be seeded from a list in advance can instead declare, in
  `output.entity_markup`, the elements its translator marks inline as it goes: assembly strips those
  elements so no markup reaches the reader, and — under `index_from: markup` — the renderer builds
  notes from the marked spans, composing with canon rather than competing with it.
  `diff_rendered_output.py` is a deterministic render/diff acceptance gate (`--accept-baseline`,
  then re-render must match). All fail-closed against symlink data-loss.

### Status & scope

- Source-language extraction is proven against **Historiettes' 17th-century French specifically**,
  not French in general; every other language config — and every other French source — ships as an
  unverified **starter preset gated by a mandatory smoke test** (see
  `plugins/literary-translator/skills/literary-translator/references/language-pair-parameterization.md`).
- The shipped `gutenberg_epub` source adapter and the expert-mode `custom` extractor remain
  **experimental / unstable** until each is pilot-proven end-to-end on a real project (see [source
  adapters](../plugins/literary-translator/skills/literary-translator/references/source-format-adapters/README.md));
  `plain_text` is specified but not yet implemented (#62).
- Scoped to texts whose natural segments / chapters fit under a configurable per-segment word cap; a
  novel with genuinely long natural chapters is out of scope for v1.
- Workflow orchestration has not been pilot-proven — a real end-to-end pilot run is still the honest
  gate before treating it as fully load-bearing; a synchronous codex block-and-hang on a DISPATCH
  call's own `await` remains an accepted residual risk (see
  `plugins/literary-translator/skills/literary-translator/references/gotchas.md`).

## `multi-profile-plugins`

Understand and diagnose how Claude Code stores plugins when several `CLAUDE_CONFIG_DIR` profiles run
on one machine. If some profiles share a `plugins/` directory (commonly via symlinks), a `claude
plugin` op from one profile re-stamps the shared `known_marketplaces.json`, and every other profile
then fails with `Marketplace 'X' has a corrupted installLocation …`; catalog-scoped garbage
collection can also delete a plugin version one profile still uses. This plugin explains the
mechanism and ships a read-only health check — it does **not** perform an automated migration.

The same shape recurs one CLI over: the Codex CLI selects its home with `CODEX_HOME`, which is how
one machine runs several OpenAI accounts side by side. That isolation decides where files are read
FROM but not what they SAY, so a profile seeded by copying the base `config.toml` inherits every
absolute path in it and is sent straight back into the original home — including `CODEX_HOME`
itself, pinned inside `[mcp_servers.*.env]`, which re-enters the base home from the MCP subprocess
while the CLI that spawned it stays correctly isolated. A second skill covers that, with its own
health check.

Trigger phrases: "corrupted installLocation", "claude plugin across profiles", "CLAUDE_CONFIG_DIR
profiles", "plugin deleted in one profile", "multi-profile plugins", "CODEX_HOME", "second codex
account", "codex profile reads the wrong home", "how much usage limit is left", "when does my limit
reset", "codex reset vouchers", "claude reset vouchers" — full lists in
`plugins/multi-profile-plugins/skills/multi-profile-plugins/SKILL.md`,
`.../skills/multi-profile-codex/SKILL.md`, and `.../skills/code-limits/SKILL.md`.

### Profiles and diagnostics

- **The Claude Code skill** — when to keep profiles' plugin stores shared vs. independent, why a
  shared store causes "corrupted installLocation" churn and cross-profile deletion, and
  (conceptually) the structural fix: give each profile its own independent `plugins/` store.
  Diagnosis-and-reasoning only; no bundled destructive tool.
- **The Codex skill** (`skills/multi-profile-codex/`) — what `CODEX_HOME` does and does not isolate,
  why `--profile` is not a substitute (it layers a config file but shares one `auth.json`), which
  config blocks to drop when seeding a new home and which to keep, and why a per-profile launcher
  belongs on `PATH` as a shim rather than in a shell rc as a function.
- **The Codex health check** (`skills/multi-profile-codex/scripts/inspect_codex_profiles.py`) —
  read-only, stdlib-only; reports whether two homes share one `auth.json` **or merely the same
  account** (distinct files, one usage pool — the quiet failure), which dotted TOML key in a config
  points into another profile's home, and which content stores (`sessions/`, `plugins/`, `cache/`,
  …) resolve to a shared target. Matching is boundary-aware: a substring test false-positives every
  sibling (`~/.codex` is a substring of `~/.codex2`) and a `startswith` test then misses the real
  pins, which sit mid-string inside `:`-joined paths and JSON blobs.
- **The usage-limit skill** (`skills/code-limits/`) — one report covering every Claude Code profile
  and Codex home, as a single table -- one row per account, its five-hour and weekly figures side by
  side, ordered by account name so a profile's rows sit together and stay put between runs -- in
  colour when it is looking at a terminal. Claude Code is read live by default, falling back to its
  on-disk usage cache only when the live read comes back empty (`--live` makes the same call and
  never falls back); Codex is read live over `codex app-server`'s `account/rateLimits/read` JSON-RPC
  call. It opens with the "usage limit reset" vouchers of each Claude Code profile read live and
  each Codex home — how many each has, and the title and expiry of the one available there, since a
  voucher lapses whether or not anyone looks. It never redeems one. An install step puts the same
  report on `PATH` as `code-limit`, as a shim that runs the shipped script rather than a second copy
  of it.
  Independent profiles are read in parallel, with up to eight readers across both vendors and
  stable report order; aliases of the same home are serialized.
- **The deep mechanism** (`references/cli-mechanism.md`) — the reverse-engineered `installLocation`
  prefix check (`path.resolve` + `startsWith`, no symlink resolution; cache-managed sources checked
  while `file`/`directory`/seed skip it), the catalog-scoped startup GC + `uninstall` that force
  full content independence rather than just a de-shared registry, the per-config-dir cache model,
  and the "a read-only probe can mutate state" caveat.
- **The health check** (`scripts/inspect_profiles.py`) — a read-only, stdlib-only Python script that
  auto-detects `~/.claude*` profiles (or takes explicit ones), reports which `plugins/` dirs are
  real vs. symlinked, flags profiles that share a `known_marketplaces.json` (the churn risk) **or a
  content store** (`cache/`/`data/`/`marketplaces/`/`.install-manifests/` — the cross-profile
  deletion vector a de-shared registry alone doesn't fix), and detects cross-profile pointer leaks
  via exact path-prefix matching (never a substring grep). Exits non-zero on any warning.

### Scope

Knowledge + read-only diagnostics, for both CLIs. Converting a shared store to independent
per-profile stores touches live plugin data, and re-seeding a Codex home rewrites a config the
desktop app also writes; both are intentionally left as deliberate, backed-up manual steps — not
automated actions this plugin performs.

## Other plugin guides

- [Database guardrails: setup and limits](../plugins/db-guardrails/README.md)
- [Usage coach: analysis and privacy](../plugins/cc-usage-coach/README.md)
- [Software localizer: workflow and adapters](../plugins/software-localizer/README.md)
