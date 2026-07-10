---
name: enduser-handbook
description: Generate and maintain an end-user handbook for any project by reading a per-project profile (`.claude/handbook/profile.yml`) that supplies language, register, stack globs, capture engine, publish target, and glossary discipline. Use when the user asks to create or update the end-user handbook / user manual, add or refresh a chapter for a feature, or re-capture handbook screenshots after a UI change. The workflow discovers the feature surface from the profile-declared route/page globs, drives the running app via the profile-declared capture command to take screenshots, authors Diátaxis chapters in the profile language and register, and publishes via the resolved publish-target adapter. NOT for developer or API documentation.
---

# End-User Handbook

## Overview

You produce an end-user handbook for the consuming project, published via the adapter named in `publish.target` and maintained over months — tone, terminology, and coverage consistency are first-class. You read the profile, every reference, and the project style guide before you author.

The **running UI is the primary source**; the code only tells you which features and routes exist. Never describe a feature you have not captured. See [references/running-ui-source.md](references/running-ui-source.md).

## When NOT to use

- Developer / API / architecture docs — those belong in `CLAUDE.md`, `AGENTS.md`, or the project's developer knowledge area.
- Internal-only tooling end users never see, unless the user explicitly asks.

## Step 0 — Read the profile (before anything else)

Read `.claude/handbook/profile.yml` (relative to the project root, i.e. the current working directory at invocation time).

- If the file does not exist, you halt with: `Missing .claude/handbook/profile.yml. Copy the example at ${plugin_path}/skills/enduser-handbook/assets/handbook.profile.example.yml, edit values for this project, and re-run.`
- If `profile_version` is unknown, you halt with a migration hint naming the supported versions.
- If a key inside a known `profile_version` is unknown, you emit a one-line warning naming the key and continue.

## Step 0a — Verify the style guide

If `style_guide.source` is set, you verify the file exists at that path (resolved from the project root).

- If the file is missing, you halt with: `style_guide.source path not found: <path> — create it or set source: null.`
- If `style_guide.source` is null and `style_guide.inline` is populated, you proceed with the inline minimal fallback and flag in your first response that the project has no formal style guide.

## Step 0b — Resolve the publish-target adapter

You read the publish-target adapter file, resolving its filename from `publish.target` by lowercasing and replacing **underscores with hyphens** (e.g. `obsidian_vault` → `obsidian-vault.md`, `static_md` → `static-md.md`): `references/publish-targets/<resolved-name>.md`. If the file does not exist you halt with: `No publish-target adapter for <publish.target>. Available: <the .md files in this directory minus README.md>.`

The resolved adapter governs every choice in W5 (paths, INDEX wiring, link syntax, frontmatter shape). Do not improvise publish wiring from memory.

## Step 0c — Pre-read mandate (every session)

Before you write any chapter prose, you read, in order:

1. The style guide resolved in Step 0a (file at `style_guide.source` or the `style_guide.inline` block).
2. Every file under `references/` (excluding `publish-targets/`).
3. The resolved publish-target adapter from Step 0b.

This pre-read is non-negotiable. It is what keeps month-over-month updates consistent.

## Hard rules (read before writing anything)

### R1 — Language register and audience

You write all prose and headings in the language at `language.code` using the register at `language.register`, pitched at the reader described by `audience.persona` and `audience.description` (their domain vocabulary, prior knowledge, and what they came to accomplish). You quote UI labels **verbatim in the language they appear in the running app** — never invent and never translate a label. Code identifiers and file slugs stay English per repo convention regardless of `language.code`.

### R2 — Anti-fabrication

Every chapter step traces to a real route AND a real screenshot the capture run produced. A step you cannot capture is disclosed in prose, never narrated. See [references/anti-fabrication.md](references/anti-fabrication.md).

### R3 — Side-effect classification

You classify every end-user-facing function as read-only · mutating-reversible · live-external · irreversible-send. The classification governs whether you capture the full flow, only the open-state, or only disclose in prose. See [references/capture-safety.md](references/capture-safety.md).

### R4 — Coverage matrix audit

Before publishing, you build a coverage matrix (function → side-effect → documented | disclosed) and confirm no row is missing. "Out of scope" means written for the reader, not silently dropped. See [references/completeness-gate.md](references/completeness-gate.md).

### R5 — Live / irreversible safety

Capture drives the REAL app wired to LIVE integrations. You stop at modal-open / read-only state and never trigger the action — the function is still covered per R4 (open-state screenshot and/or disclosure). The project's concrete forbidden-trigger examples live in `capture.live_action_examples`; if empty, the principle still binds. See [references/capture-safety.md](references/capture-safety.md).

### R6 — PII

You do not publish screenshots containing personally identifiable information. Capture the read-only state with PII out of frame, mask it, or disclose in prose. The project's concrete categories live in `capture.pii_categories`; if empty, the principle still binds. See [references/capture-safety.md](references/capture-safety.md).

## Workflow

### W1 — Discover the feature surface

You list the routes/pages declared in `stack.backend.route_globs` and `stack.frontend.page_globs` to identify the page, its route, the required role from `capture.auth_role_enum`, and the post-mount data signal the page waits on (the engine-specific recipe lives in `capture.page_identity_signal`). Read the globs through the framework idioms named in `stack.backend.type` and `stack.frontend.type` — e.g. a Laravel route file groups differently than a Next.js `app/` tree, a Vue single-file component exposes its route differently than a React page. For client-rendered pages the data-readiness signal is typically an XHR to `stack.backend.api_url_prefix`; reference that prefix when you set the per-chapter wait condition. Server-rendered pages have no post-mount XHR — assert page identity on a heading/DOM element instead (see [references/page-identity.md](references/page-identity.md)).

For each role in `capture.auth_role_enum`, you check `capture.role_flags[role]`. A granted flag only means the *control renders* — not that the role actually has the data. Three independent capture-feasibility gates apply per function:

- **Permission flag** — granted in `capture.role_flags[role]`?
- **Seed/staging data state** — does data exist to make the overlay non-empty/triggerable?
- **Side-effect** — read-only / mutating / live-external / irreversible (per R3 + R5)?

Then you enumerate the interactive surface within the page — root and component subtree — and build the coverage matrix (R4). You enumerate from the running DOM rather than the source, capturing each control's verbatim text, title, aria-label, href, and role — icon-only controls included. [assets/surface-audit.playwright.ts](assets/surface-audit.playwright.ts) is a non-normative reference implementation of this enumeration for the Playwright reference case; the methodology is normative and engine-agnostic — a different engine reimplements the driver glue against its own API, reusing the engine-neutral `assets/lib/*.mjs` helpers as-is. A function you cannot fully capture is not dropped; it is disclosed in prose, and the blocking flag/state is recorded.

You record the chapter entry in the capture manifest per [references/manifest-discipline.md](references/manifest-discipline.md). The manifest must have a step for every capturable end-user-facing overlay — not just top-level page states.

### W2 — Capture screenshots

You drive the capture using `capture.engine` and run `capture.command` exactly as written in the profile. The harness runs container-only per [references/container-isolation.md](references/container-isolation.md) — you never run the engine on the host.

After navigation to each page, you apply: **{{capture.page_identity_signal}}**. This sentence is the profile's engine-aware recipe; you translate it into the appropriate engine API call given `capture.engine` and `capture.command`. The principle (assert page identity before screenshot, not just URL) lives in [references/page-identity.md](references/page-identity.md) and applies even if the field is empty.

You write captured assets to `capture.output_dir`. The capture pins the sandbox locale to `capture.locale` (a full POSIX locale such as `de_DE.UTF-8`), which drives both process locale and UI language in the running app; the bare content-language code lives in `language.code`. Live / irreversible / PII constraints from R5 and R6 bind every step — see [references/capture-safety.md](references/capture-safety.md). The reusable capture-spec helper contract — fail-closed request guard, page-identity assertion, reproducible mask + leak-assert, safe dialog dismiss — lives in [references/capture-spec-helpers.md](references/capture-spec-helpers.md).

### W3 — Author the chapter

You write the chapter in `language.code` using `language.register`. You start from [assets/chapter-template.md](assets/chapter-template.md) and substitute section labels from `publish.section_labels` (e.g. `publish.section_labels.prerequisites`, `publish.section_labels.related`) so the headings render in the chapter's language.

You write Diátaxis content per [references/diataxis.md](references/diataxis.md), restricted to the quadrants in `diataxis.quadrants_in_use`. You embed each saved screenshot by relative path with alt text in `language.code`. You quote exact UI strings verbatim per R1.

Tone steady over months requires the per-session exemplar re-read per [references/tone-consistency.md](references/tone-consistency.md).

### W4 — Glossary

For every domain term used, you ensure a canonical entry exists at `publish.glossary_dir`; you add it if missing. The canonical term language is `glossary.canonical_term_language`. The synonym field in each entry uses the localized field name `glossary.synonym_field_name`. When `publish.glossary_seed` is set and readable, new glossary entries are seeded from it (the project's glossary index/dashboard). You link terms from the chapter using the link syntax the publish-target adapter prescribes. See [references/glossary-discipline.md](references/glossary-discipline.md).

If `glossary.english_code_required` is true, every entry carries an English code identifier alongside the localized canonical term — useful for cross-language search and for matching code identifiers.

### W5 — Publish

You read the publish-target adapter file resolved in Step 0b — `references/publish-targets/<resolved-name>.md`, where `<resolved-name>` is the `publish.target` value lowercased with underscores replaced by hyphens (`static_md` → `static-md.md`) — and you follow its wiring exactly. The adapter tells you:

- Where chapter files land — you write the chapter to `publish.chapters_dir/<slug>.md`.
- Where INDEX / dashboard / log files live and how to wire them.
- Frontmatter shape and whether `publish.frontmatter_required` is honored.
- Link syntax (e.g. wikilinks vs markdown links, governed by `publish.wikilinks`).
- Any project-knowledge notes (e.g. updating a `CLAUDE.md` note, a session log).

Assets remain at `capture.output_dir`. You never improvise the publish wiring from memory — every adapter-specific detail comes from the adapter file.

### W6 — Revalidation / audit mode (existing chapters)

When you re-validate an already-merged chapter rather than authoring a new one, you re-derive the feature surface from the running UI (as in W1) and diff it against the existing chapter and its manifest entry, then classify each delta as no-op, accepted-diff, or material. Revalidation skips only the initial accepted-manifest review for no-op or accepted-diff unchanged scope. Any material delta — to route, role, steps, glossary terms, side-effect class, or a changed/added/removed control or newly discovered interactive trigger — emits a delta manifest and halts for user acceptance per [references/manifest-discipline.md](references/manifest-discipline.md). See [references/revalidation.md](references/revalidation.md).

## Consistency over time

The handbook lives for years. Before each session you re-read the style guide, re-read the glossary index at `publish.glossary_seed` when it is set and readable, and open the latest exemplar chapter per [references/tone-consistency.md](references/tone-consistency.md). After writing you run the coverage audit per [references/completeness-gate.md](references/completeness-gate.md) — silent omissions are defects, not stylistic choices.
