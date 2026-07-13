# Lazy Ants — Claude Code plugins

Public plugins for [Claude Code](https://claude.com/claude-code), maintained under the `Lazy Ants` brand.

## Plugins

| Plugin | Version | What it does |
|---|---|---|
| [`ai-cli-optout`](#ai-cli-optout--v111) | 1.1.1 | Opt out of telemetry across every locally installed AI CLI / AI-enabled IDE, plus Vercel CLI and macOS / Windows OS-level privacy surfaces. |
| [`db-guardrails`](#db-guardrails--v100) | 1.0.0 | Stop AI coding agents from accidentally emptying your database — an always-on hook that blocks destructive DB commands across 15+ frameworks, plus a stack-aware installer for deeper safety layers. |
| [`obsidian-project-vault`](#obsidian-project-vault--v100) | 1.0.0 | Set up, migrate, audit, and operate an Obsidian vault as an LLM Wiki — a persistent, compounding knowledge base maintained by Claude Code. |
| [`cc-usage-coach`](#cc-usage-coach--v100) | 1.0.0 | Personalized, behavior-aware analysis of where your Claude Code (Max/Pro) usage-limit tokens go, with ranked, low-effort ways to use fewer — computed entirely from your local session logs. Python measures; Claude concludes. |
| [`enduser-handbook`](#enduser-handbook--v130) | 1.3.0 | Author, capture, and publish a Diátaxis-structured end-user handbook for any project — methodology shipped as a reusable skill, project-specific bindings supplied via `.claude/handbook/profile.yml`. |
| [`literary-translator`](#literary-translator--v141) | 1.4.1 | High-fidelity literary book translation over a Gutenberg-style EPUB source (expert-mode `custom` extractor also supported) — a codex-translate → deterministic false-green gate → codex-review → Claude-fix loop run to convergence, with a frozen name/realia canon, a configurable verse policy, and ledger-based resumability. v1.1 adds optional book assembly into an Obsidian glossary-wiki behind a deterministic render/diff gate. |

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

## `ai-cli-optout` — v1.1.1

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

- **Anthropic Claude Code users:** setting `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` or `DISABLE_TELEMETRY=1` does more than stop telemetry — both flags also disable the `/remote-control` command and, on Max / Team / Enterprise plans, silently switch Opus 4.6 off the 1M-context default model. See [anthropics/claude-code#34178](https://github.com/anthropics/claude-code/issues/34178) and #142. The skill gates both edits behind `requires_confirmation: true` so you see the trade-off and can decline. A safe narrow opt-out that leaves these features intact (`DISABLE_ERROR_REPORTING=1`, `DISABLE_FEEDBACK_COMMAND=1`, `CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1`, `skipWebFetchPreflight: true`) is documented in #142.
- **Cursor:** quit the app before the skill edits any JSON — Cursor's Electron process rewrites settings on graceful quit and will overwrite your changes.
- **Antigravity:** the AI-training opt-out is **email-only** to `antigravity-support@google.com`; no CLI / setting exists. The skill surfaces this in its report but cannot automate it.
- **Vercel CLI:** the subcommand `vercel telemetry disable` is persistent; `VERCEL_TELEMETRY_DISABLED=1` is per-run only and does **not** change the persisted status reported by `vercel telemetry status`. The skill applies both so you're covered either way.
- **Vercel Claude Code plugin (separate from the Vercel CLI):** if installed, it sends every bash command string to `telemetry.vercel.com` by default. Interim opt-out: `VERCEL_PLUGIN_TELEMETRY=off`. Not yet a first-class vendor in this skill — tracked in #144.

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

## `enduser-handbook` — v1.3.0

Author, capture, and publish a Diátaxis-structured end-user handbook (tutorials, how-tos, reference, explanation) for any project. The methodology — pre-read mandate, anti-fabrication rules, capture safety, page identity, manifest discipline, glossary and tone consistency, completeness gate, "running UI is the primary source" — ships as a reusable skill. Project-specific bindings (language, register, stack globs, capture engine, publish target, glossary) live in `.claude/handbook/profile.yml` so the same skill produces a German shopkeeper-register handbook for one project and an English developer-register handbook for the next without forking the workflow.

Sibling to [`obsidian-project-vault`](#obsidian-project-vault--v100): where `obsidian-project-vault` builds the *internal* LLM Wiki that the team and Claude Code use, `enduser-handbook` builds the *external* end-user manual that ships to customers. They compose — `enduser-handbook`'s default publish-target adapter is `obsidian-vault`, so the handbook can be written straight into a vault scaffolded by `obsidian-project-vault` (separate folder, separate INDEX wiring, separate frontmatter shape). One vault, two audiences.

Trigger phrases: "write the end-user handbook", "update the user manual", "add a handbook chapter for <feature>", "re-capture handbook screenshots", etc. — full list in `plugins/enduser-handbook/skills/enduser-handbook/SKILL.md`.

### What it covers

- **Profile-driven** — language, register, stack/route globs, capture engine, publish target, glossary discipline all declared in `.claude/handbook/profile.yml`. The skill halts loudly if the profile is missing or unknown rather than guessing.
- **Running UI is the source** — code only tells the skill *which* features and routes exist; every described feature must be captured live, never fabricated from the codebase.
- **Diátaxis structure** — tutorials, how-tos, reference, and explanation each have their own discipline; chapters are gated on completeness before publish.
- **Publish-target adapters** — currently `obsidian-vault` and `static-md` (universal plain-Markdown) — paths, INDEX wiring, link syntax, frontmatter shape governed by the adapter, not improvised.
- **Month-over-month consistency** — mandatory pre-read of style guide + every reference file every session, so tone and terminology stay stable as the handbook grows.
- **Reference capture tooling (v1.0.5)** — ships non-normative Playwright reference implementations for the parts that carry the most risk: a live-DOM surface enumerator (every control's verbatim text/title/aria-label/href/role, icon-only controls included) and a context-level capture guard (fail-closed request classifier, service-worker block, WebSocket/beacon/SSE handling, safe Escape-first dialog dismiss, reproducible PII mask + leak-assert). The methodology stays engine-agnostic — reimplement the driver glue for another engine; the engine-neutral `assets/lib/*.mjs` helpers are reused as-is.
- **Revalidation / audit mode (v1.0.5)** — a first-class path for re-validating an already-merged chapter: re-derive the surface from the running UI, diff against the existing chapter and manifest, and classify each delta (no-op / accepted-diff / material) — material deltas still emit a delta manifest and halt for review.
- **Surface / guard / capture hardening (v1.0.6)** — the surface enumerator now also catches framework glyph/icon controls (`.btn`, `[data-bs-toggle]`, `[data-toggle]`) and records each control's `class` to drive destructive-action hints; the capture guard adds a `'benign'` verdict so blocked dev-telemetry (e.g. laravel-boost, Sentry) no longer false-trips the safety assertion; `captureRegion` gains a `maxHeight` cap for runaway-height modals; and the completeness gate ships a concrete disclose trigger-list + prose templates.
- **Capture-safety correctness (v1.1.1)** — a broken `<img>`'s `alt` text *is* painted into the screenshot (browser replacement-rendering), so it is reclassified from a "non-rendered attribute" to a painted-but-unscannable surface the eyeball backstop must catch — only `title`/`aria-label` are genuinely non-rendered; and the mask/leak-scan scope rule is made explicitly bidirectional — scope must equal the captured frame, so a full-viewport shot scopes the scan to the document root (an element- or region-scoped shot to its own node) and framed app chrome (e.g. a header user name) is never left unscanned.
- **Capture-determinism guardrails (v1.1.1)** — `page-identity.md` now warns against four author-time traps that ship a wrong or broken shot while the run still looks green: asserting visibility on a layout wrapper that collapses to zero height (assert a content-bearing child, not a bare container); capturing a mid-animation frame (settle transitions / disable animations first); a full-element shot of lazy-loaded or virtualized content shipping blank below-the-fold rows (scroll to load first); and a deliberately staged data-state silently reverting (pair every precondition with a fail-closed assertion that it held).
- **Capture-guard and audit hardening (v1.1.2)** — the dangerous-verb detector now percent-decodes to a fixed point, so a doubly-encoded destructive verb in a GET can no longer slip past the deny step; `dismissModal` waits for the dialog to actually hide after Escape; the coverage matrix labels a native `<input type=submit value=Delete>` instead of printing `(unlabelled control)`; and the module's real Playwright floor (>= 1.51) is documented.
- **Authoring ergonomics + coverage (v1.2.0)** — a dependency-free profile validator and `profile_version` reader (`assets/lib/profile-version.mjs` + a normative JSON-Schema) checks the profile at Step 0 without a YAML parser; a new `/scaffold-profile` command generates `.claude/handbook/profile.yml` interactively from an auto-detected, user-confirmed stack; capture now covers **real** empty / error / denied state variants (never synthesized) behind a fail-closed `state` marker on `assertIdentity`; an optional per-role surface re-audit diffs the interactive surface between roles on a PII-free structural key (`tag / role / name / data-testid`); and a `capture-engines.md` reference documents the Playwright / Cypress / Puppeteer / manual recipes and their guard obligations.
- **Cross-line structural profile validation (v1.3.0)** — the dependency-free `profile_version` scan now catches two additional structural error classes, both provably false-reject-free (differential-tested against Ruby's Psych, never halting a document a real YAML parser would load): mechanism A (an unterminated flow collection or quoted scalar anywhere in the document) and mechanism C (an alias to an undefined anchor, in a document with no `&anchor` defined at all). Mechanism B (invalid dedent, including through the block-scalar `capture.command: |` shape) is deliberately deferred — it reintroduces the mini-YAML-parser mis-parse risk the scan otherwise avoids — and is tracked as a follow-up.

### What it is **not**

- Developer / API / architecture docs — those belong in `CLAUDE.md`, `AGENTS.md`, or the project's internal knowledge area (e.g. an `obsidian-project-vault` wiki).
- A one-shot generator — it is a long-lived authoring loop maintained over the project's lifetime.

### Tips for best results

- **Plan first, then go wide.** In Claude Code, sketch the chapter plan before writing anything (plan mode), then drive authoring and review at high effort with multi-agent orchestration (e.g. `ultracode`) so several agents capture, cross-check, and validate coverage in parallel instead of one linear pass.
- **One page at a time.** Author and capture a single chapter per pass and keep its scope tight. A focused page is far easier to get right — and to verify — than a sprawling one; resist bundling unrelated features into one chapter.
- **Review from more than one perspective.** Have several agents read the drafted chapter, each from a different angle (a first-time user, a power user, a skeptic hunting for fabricated or undocumented behavior). More viewpoints beat one — no single pass catches everything.
- **Rerun and validate coverage.** When a chapter (or the whole handbook) is done, run the skill again as a completeness pass: walk the actual feature surface and confirm every feature is described. The first pass always misses some.

## `literary-translator` — v1.4.1

High-fidelity literary **book translation** over a Gutenberg-style EPUB source (or, in expert mode, a hand-co-designed custom extractor for any other source shape; a `plain_text` adapter is specified but not yet implemented, #62): a `codex-translate → deterministic false-green gate → codex-review → Claude-fix` loop, run to convergence per segment, with a frozen name/realia **canon**, a configurable **verse policy**, and **ledger-based resumability**. The loop runs per segment / per novella, never per book. v1.0 delivered converged per-segment drafts plus a full audit trail; **v1.1 adds optional book assembly + output rendering** — the converged drafts assemble into an Obsidian glossary-wiki (keyed on the frozen canon) behind a deterministic render/diff acceptance gate. **v1.2 hardens Workflow-orchestration reliability** — the review and glossary-pass steps now follow the same fire-and-forget-dispatch + bounded-poll + disk-is-truth pattern already proven for translate, closing a class of schema-shape failures and unbounded-hang risk in the underlying Workflow templates. **v1.3 fixes a verse×footnote correctness cluster** — poems without `.line` stanza children, verses nested in heading-wrapping `<div>`s, and footnotes cited inside a verse now extract, segpack, validate, assemble, and render correctly. **v1.3.1 hardens two W1-adjacent authoring gates and closes a doc-prose leak** — `scaffold_validate.py`'s W1 gate now rejects unfilled bracket placeholders in hand-adapted planning docs, and two reference docs no longer point the reader at a non-shipped internal path. **v1.3.2 is a bugfix release** — a wrong regen hint, sentinel-token pollution in the language smoke gate, and a canon whole-file hardening gap are fixed. **v1.3.3 is an output-layer polish + first-run robustness patch** — a Unicode-line-boundary false-mismatch in the render/diff gate, a case-/normalization-insensitive entity-note filename collision, an undeclared Python 3.10 floor, an un-preflighted `import yaml`, a no-op foreign-remainder stopword check, and a double-wikilink cosmetic bug are all fixed. **v1.3.4 continues the verse×footnote correctness cluster** — a dedicated verse block now renders every verse it carries (not just the first), a skip-policy footnote cited only inside a mode-voided verse no longer deadlocks assembly, a footnote nested inside a verse-in-a-footnote-definition (to arbitrary depth) is now discovered and carried through, and an embedded verse that is a prose block's entire content renders as a blockquote. **v1.3.5 hardens the W3 glossary pass** — a new `glossary_batch_plan.py` planner excludes candidates already in canon `entries{}` **and** `review_queue` before re-batching (closing a resumability gap that re-researched queued names every run), a preflight cost cap plus a recalibrated `batch_agent_cap` default stop the glossary/mass batches from either over-spending or refusing a normal-length novel, and a capitalized-elision ambiguity (`L'Enclos` vs the article-elided `Enclos`) is now surfaced to the glossary adjudicator as a review-queue flag rather than by widening `ELISION_RE` (which would re-break fixed compounds like `D'Artagnan`). **v1.3.6 fixes a first-scaffold convergence blocker plus two smaller gaps** — the seed `style_bible.template.md` now ships the `STYLE_CONTRACT_BEGIN`/`STYLE_CONTRACT_END` markers that `compute_style_contract_hash` requires (their absence deterministically produced "0 converged" / `ledger-write-failed` on every freshly scaffolded project, with clean drafts sitting on disk), guarded early by a new W1 `scaffold_validate.py` gate; the glossary pass gains a rule keeping a nickname/epithet/alias on its own transliteration rather than the referent's real name; and the shipped extractor's fatal helpers are annotated `-> NoReturn`. **v1.3.7 is a canon-enforcement + transient-recovery + review-gate correctness cluster** — the frozen `canonical_target_form` now actually reaches the translate/review prompts via a new segpack `canon_map` (with a declined-stem rule so a correctly inflected canonical name is never flagged), transient/mechanical mass-translate failures (poll timeouts, a fix call that dies/hits the output-token ceiling/is classifier-blocked on a valid draft) become recoverable instead of human-escalated, the review-artifact compare no longer terminal-blocks on a free-text transcription slip, and an infra-fabricated sentinel-`loc` review verdict is rejected before it false-blocks a clean draft. **v1.4.0 adds a fifth canon `basis` value, `sense_translated`** — a speaking / meaningful name rendered by sense rather than transliteration is now lockable in the frozen canon with a `canonical_target_form` (previously such names were re-parked in the review queue every run), guarded on mid-pipeline resume by a new `glossary_preflight.py` staleness gate that halts before dispatch if a project's durable schema copy predates the 1.4.0 contract. **v1.4.1 is a documentation-and-gate hardening patch** — the `plain_text` source-adapter fiction is reconciled across every reference doc, marketing description, and code comment that presented it as a working/shipped adapter (it remains specified but not yet implemented, #62); the W3 language-smoke `pass:true` framing is corrected to be honest about uncased-script (Hebrew/Yiddish/Arabic) blind spots, and its count-based "completeness" label is renamed to what it actually enforces (an entry-count, dedup-blind floor); and `validate_draft.py`/`draft_ready.py` gain a draft `seg`-identity check that rejects a mislabeled/cross-wired draft instead of certifying it `OK`/`READY`.

Trigger phrases: "translate this book", "set up a literary translation pipeline", "new book translation project", "translate this EPUB/story collection from X to Y", "Gutenberg EPUB translation", "resume book translation" — full list in `plugins/literary-translator/skills/literary-translator/SKILL.md`.

### What it covers

- **Engine loop** — per segment: codex translates, a deterministic false-green gate (`validate_draft.py`) rejects placeholder / empty / policy-violating drafts, codex reviews, Claude applies fixes, looped until converged. The scripts surface candidates and enforce schemas; the accuracy / identity calls are codex's, never a script's.
- **Frozen name/realia canon** — a 1:1 `source_form → canonical_target_form` dictionary (`canon.json`) with a validation gate (`canon_validate.py`) and an opt-in human-adjudication gate (`canon_adjudication_audit.py`) that turns duplicate / merge / missed-pair / unresolved-queue review requirements into a persisted, machine-checkable record.
- **Verse policy** — configurable handling of verse vs prose (`rendered` / `literal_gloss` fields, per-mode validation).
- **Ledger-based resumability** — a `ledger.json` with a composite cache key so an interrupted run resumes safely and re-applies any style-bible / canon edit rather than shipping stale drafts.
- **Source adapters** — `gutenberg_epub` (the one working built-in adapter) plus an expert-mode `custom` extractor (supported, experimental); `plain_text` is specified but not yet implemented (#62). Scripts are self-anchored and stdlib-first; each emits one JSON line to stdout, human detail to stderr, exit 0 / 1 / 2.
- **Book assembly + output rendering (v1.1)** — once every in-scope segment is converged, `assemble.py` joins the manifest + per-segment drafts + verse map (ledger-gated on `converged` + sha1 match) into a target-agnostic NodeStream, and an output adapter renders it. The shipped `obsidian` adapter produces a vault of chapter notes with folder-qualified `[[wikilinks]]`, footnotes, and verse blocks, plus one entity note per canon entry (canon IS the entity registry). `diff_rendered_output.py` is a deterministic render/diff acceptance gate (`--accept-baseline`, then re-render must match). All fail-closed against symlink data-loss.
- **Workflow-orchestration reliability (v1.2)** — review and the glossary-pass batch call now follow translate's proven dispatch → bounded-poll → disk-read pattern instead of a bare, unbounded codex call, closing a hang risk a real 11-teammate incident on the source project already surfaced. Every Workflow-tool agent schema is a flat, tool-use-API-legal top-level object (a top-level `oneOf`/`array` schema simply cannot be an `agent()` schema — the earlier shape blocked review and glossary dispatch outright). Concurrent glossary batches write to their own run-scoped fragment instead of racing on a shared `canon.json`, with one serialized merge plus an independent disk-verification pass before the pass is trusted complete. A run-scoped token on every fire-and-forget artifact, checked at every point it's consumed or committed, closes a class of stale-artifact-from-an-interrupted-run bugs; whether to resume a run at all is gated by a dedicated input/version digest.
- **Verse×footnote correctness cluster (v1.3)** — the extractor now handles poems whose stanzas lack `.line` children and verses nested in heading-wrapping `<div>`s; footnotes cited inside a verse are recorded and carried through segpack/validate/assemble/render instead of being dropped or left as a dangling definition; a shared verse×footnote fixture corpus exercises the full chain end-to-end.
- **Authoring-gate hardening + doc-prose leak fix (v1.3.1)** — `scaffold_validate.py`'s W1 gate now rejects unfilled bracket placeholders (`[SOURCE LANGUAGE]`, `[PROJECT TITLE / AUTHOR / PERIOD -- fill in]`, ...) in hand-adapted planning docs via a closed-list, whitespace-normalized scan, without risk of blocking legitimate editorial brackets like `[NOTE]`/`[SIC]`; the companion ERA/DOMAIN trap-string check gained a co-occurrence-based scan catching mangled/partial trap examples; and two reference docs no longer instruct the reader to read a non-shipped internal path directly, with the drift guard extended to scan doc prose as well as scripts.
- **Bugfix release (v1.3.2)** — the `select_segments.py` regen hint for a stale `derivation_bundle_hash` now names the correct steps (`bootstrap_names.py` and the glossary pass, not `segpack.py`, which only ever copies the hash forward); the language smoke gate strips `⟦FNREF_N⟧`/`⟦VERSE_…⟧` sentinels before candidate extraction and density scoring so they can no longer corrupt or false-fail the gate; and `canon_validate.py` now rejects (both at merge time and at `--verify-merged`) a `source_form` present in both `entries{}` and `review_queue[]`.
- **Output-layer polish + first-run robustness (v1.3.3)** — the render/diff acceptance gate's baseline reader now mirrors the writer's exact newline-splitting semantics, so a rendered line containing a Unicode line-boundary character (U+2028/U+2029/NEL/...) no longer false-mismatches forever; entity-note filenames are de-duplicated on NFC-normalized casefold, closing a silent note-clobber on case-/normalization-insensitive filesystems (macOS APFS, Windows); `assemble.py`'s one runtime-evaluated PEP-604 union is now a quoted forward reference (with a permanent AST-based drift guard against future regressions), fixing a hard crash on Python ≤3.9; `render_obsidian.py`'s `import yaml` now preflights like every sibling script instead of raw-tracebacking on a missing dependency; the foreign-remainder stopword check no longer silently no-ops on punctuation-adjacent tokens; and a name appearing in both an inline verse and its host prose no longer renders two wikilinks.
- **Verse×footnote correctness cluster, round 2 (v1.3.4)** — `render_obsidian.py` no longer renders only the first entry of a multi-verse `kind:"verse"` node (a silent whole-content data-loss bug, #119); under `verse_policy.mode: skip`, a footnote whose only citation site is a mode-voided verse's content no longer deadlocks whole-book assembly (`orphan_footnote_def`), with the exemption driven by the manifest's mode-independent `verse.store` ground truth and extended so a verse embedded in the exempted footnote's own definition isn't then false-orphaned (#118 item 1); a footnote cited inside a verse that is itself embedded in another footnote's definition — to arbitrary nesting depth — is now discovered by a segpack worklist/fixed-point and a de-duplicated recursive assemble helper, referenced-only so nothing dangles (#118 item 2); and an embedded verse that is the entire content of a prose block now renders as a blockquote instead of inline italic (#118 item 3).
- **W3 glossary-pass resumability, cost guardrails & elision adjudication (v1.3.5)** — a new `glossary_batch_plan.py` owns candidate→batch curation: it excludes any candidate already in canon `entries{}` **or** `review_queue` (with an explicit `--retry` override) before re-batching, so a resumed glossary pass no longer re-researches names a prior run already queued (#101), and emits a `no_new_candidates` marker that skips the pass entirely when nothing is left to do. The glossary-pass Workflow gained a preflight cost cap (`3·batches + 2` estimated calls vs `engine.batch_agent_cap`), and the shipped `batch_agent_cap` default was recalibrated 1000→3500 so a normal-length novel is admitted rather than refused on the first run (#95). A capitalized elided article that fuses onto a name (`L'Enclos` where `Enclos` is itself a candidate) is now detected in `bootstrap_names.py` and flagged `elision_ambiguous` for the glossary adjudicator to route to `review_queue` — resolving the ambiguity at the adjudication stage instead of widening `ELISION_RE`, which a prior attempt showed re-breaks fixed compounds like `D'Artagnan`/`L'Aquila` (#91).
- **First-scaffold convergence blocker + glossary & typing fixes (v1.3.6)** — `style_bible.template.md` now ships the `STYLE_CONTRACT_BEGIN`/`STYLE_CONTRACT_END` markers wrapping style_contract sections A–F that `cache_key.py`'s `compute_style_contract_hash` hard-requires; without them every freshly scaffolded project translated and reviewed cleanly to disk but recorded "0 converged" (`ledger-write-failed`) on every segment — an opaque hard blocker whose root cause was two missing comment lines, now also caught early by a fourth `scaffold_validate.py` W1 gate that mirrors the hash consumer's exact marker byte-strings and failure conditions (#129). The glossary pass gained a canonicalization rule preventing a salon nickname/epithet/alias from inheriting its referent's real-name `canonical_target_form` — only true orthographic variants of one surface name may share a form (#134). And `extract.py.template`'s fatal-abort helpers (`_missing_dep`, `die`) are annotated `-> NoReturn`, silencing spurious Pyright "possibly unbound" warnings on the optional-dependency imports (#136).

### Status & scope

- Source-language extraction is proven against **Historiettes' 17th-century French specifically**, not French in general; every other language config — and every other French source — ships as an unverified **starter preset gated by a mandatory smoke test** (see `plugins/literary-translator/skills/literary-translator/references/language-pair-parameterization.md`).
- The shipped `gutenberg_epub` source adapter and the expert-mode `custom` extractor remain **experimental / unstable** until each is pilot-proven end-to-end on a real project (see `references/source-format-adapters/`); `plain_text` is specified but not yet implemented (#62).
- Scoped to texts whose natural segments / chapters fit under a configurable per-segment word cap; a novel with genuinely long natural chapters is out of scope for v1.
- The v1.2 Workflow-orchestration reliability pass is new plugin hardening, not itself pilot-proven — a real end-to-end pilot run is still the honest gate before treating it as fully load-bearing; a synchronous codex block-and-hang on a DISPATCH call's own `await` remains an accepted residual risk (see `references/gotchas.md`).

## License & disclaimer

MIT — see [`LICENSE`](./LICENSE). No warranty, no vendor affiliation — see [`DISCLAIMER.md`](./DISCLAIMER.md).
