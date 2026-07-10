---
description: Scaffold .claude/handbook/profile.yml via a short interview
allowed-tools: Read, Glob, Grep, Write, Edit, AskUserQuestion
disable-model-invocation: true
---

# Scaffold a handbook profile

You guide the user through creating `.claude/handbook/profile.yml` — the project-specific
binding the `enduser-handbook` skill reads at its Step 0 — by detecting what you can, asking
for what you cannot, and writing a filled copy of the plugin's canonical example. You never
run the enduser-handbook skill and you never drive the running application: this command only
reads the project tree and writes two files.

Canonical sources you read from (never re-embed their key sets — always copy them fresh):

- `${CLAUDE_PLUGIN_ROOT}/skills/enduser-handbook/assets/handbook.profile.example.yml` — the
  profile's full key set, comments included.
- `${CLAUDE_PLUGIN_ROOT}/skills/enduser-handbook/assets/style-guide.example.md` — the style
  guide stub template.

Files you write, both relative to the project root (the current working directory):

- `.claude/handbook/profile.yml`
- `.claude/handbook/style-guide.md`

## Step 1 — Detect the stack (Glob-verified, then confirmed — never fabricated)

Glob the project root for framework markers and read `composer.json` / `package.json` as
plain text (you are reasoning over JSON, not running a parser):

- `composer.json` dependency `laravel/framework` → `stack.backend.type: laravel`.
- `manage.py` present, or a Django marker in `requirements.txt` → `django`.
- `Gemfile` naming `rails` → `rails`.
- `package.json` dependency `next` → `stack.backend.type: nextjs` **and**
  `stack.frontend.type: react` (Next.js ships React; confirm this pairing with the user rather
  than assuming it).
- `package.json` dependency `fastapi` (or a Python `fastapi` import) → `fastapi`.
- `pom.xml` / `build.gradle` with a `spring-boot` marker → `spring`.
- `package.json` dependency `vue`, `react`, `svelte`, or `@angular/core` (absent an nextjs
  pairing already decided above) → the matching `stack.frontend.type`.
- Nothing matches → `none` for the layer that found nothing. Do not guess.

For each detected type, propose the matching `route_globs` / `page_globs` from the framework's
usual layout (e.g. Laravel → `routes/web/*.php`, Next.js → `app/**/page.tsx`) and Glob-verify
each proposed pattern against the real tree. Report the match count next to every proposed
glob. A glob with zero matches is flagged, not asserted — surface it in the confirmation round
below instead of writing it silently.

Detection only proposes; nothing detected here is written to the profile until the user
**confirms** it in Step 2. If detection is ambiguous or a marker is missing, ask rather than
guess — an unconfirmed `stack.*` value is worse than an admitted unknown.

## Step 2 — Interview (batched `AskUserQuestion`, ≤4 questions per round)

Run at least two batched rounds. Suggested grouping:

**Round A — identity and audience**
- `language.code` (ISO-639-1) and `language.register` (e.g. `formal_Sie`, `informal_tu`,
  `formal_you`).
- `audience.persona` (short label) and `audience.description`.
- The country the reader is in (drives Step 3's locale derivation — do not ask for
  `date_format` / `decimal_separator` / `currency_symbol` directly).

**Round B — confirm the detected stack**
- Present every `stack.*` value and glob from Step 1, including zero-match globs, and let the
  user accept, correct, or fill in what detection could not resolve. Confirm the Next.js →
  React pairing explicitly if it applies.

**Round C — capture and publish**
- `capture.engine` (`playwright` | `cypress` | `puppeteer` | `manual`) and `capture.command`
  (the exact, copy-pasteable command the project uses to run it — ask for it verbatim; do not
  invent a docker/CI invocation).
- `publish.target` — only the two shipped adapters are valid answers: `obsidian_vault` or
  `static_md`.

Every interview answer is confirmed before use, never assumed from a default.

## Step 3 — Derive dependent values (shown for confirmation, not asked separately)

- `capture.locale` from the confirmed country (e.g. Germany → `de_DE.UTF-8`, United States →
  `en_US.UTF-8`). If the country is ambiguous, ask — do not fabricate a locale.
- **Locale sync inside `capture.command`.** Copy the user's `capture.command` verbatim, but if
  it contains `LANG=`/`LC_ALL=` tokens, regenerate those two tokens from the confirmed
  `capture.locale` rather than keeping whatever locale the pasted command happened to carry.
  `capture.locale` and the `LANG`/`LC_ALL` values inside `capture.command` must always agree —
  state this rule to the user when you show the derived command.
- `language.date_format`, `language.decimal_separator`, `language.currency_symbol` from the
  same confirmed country (e.g. United States → `MM/DD/YYYY` / `.` / `$`). Never carry over the
  canonical example's German defaults (`DD.MM.YYYY` / `,` / `€`) for a non-German project —
  derive-and-confirm instead.
- `publish.wikilinks`: `true` when `publish.target` is `obsidian_vault`; **`wikilinks: false`**
  when `publish.target` is `static_md` (the static adapter halts otherwise — see
  `references/publish-targets/static-md.md`).
- `capture.output_dir` set **under** `publish.chapters_dir` for both publish targets (e.g.
  `chapters_dir: vault/handbook` → `output_dir: vault/handbook/assets`), matching the
  canonical example's layout.
- `publish.section_labels.prerequisites` / `.related` localized to `language.code`.
- `glossary.canonical_term_language` = `language.code`; `glossary.synonym_field_name`
  localized (e.g. `Synonyme` for German, `Synonyms` for English);
  `glossary.english_code_required: true`.
- `style_guide.source` = `.claude/handbook/style-guide.md` (the stub this command writes in
  Step 6 — this satisfies the skill's Step 0a existence check).

Values the interview and detection round did not determine stay at the canonical example's
documented default, marked `TODO:` in the written file, so the user edits them later rather
than receiving a fabricated project-specific guess. `capture.auth_role_enum`, `role_flags`,
`live_action_examples`, `pii_categories`, `manifest_path`, `capture_specs_dir`,
`page_identity_signal`, `diataxis.quadrants_in_use`, `publish.frontmatter_required`, and
`style_guide.inline` are the fields most often left at the default — say so explicitly in your
summary before writing.

## Step 4 — Clobber-safety check (before any Write)

For each of the two target files, Read/Glob-check whether it already exists:

- If `.claude/handbook/profile.yml` **already exists**, do not overwrite it. Write your
  generated profile to `.claude/handbook/profile.yml.new` instead, then use `AskUserQuestion`
  to ask the user whether to keep the sidecar for a manual merge, overwrite the original now,
  or abort — recommend the non-destructive sidecar by default.
- Apply the identical rule, independently, to `.claude/handbook/style-guide.md` — if it
  **already exists**, write `.claude/handbook/style-guide.md.new` and ask.

A tuned profile or a real style guide the project already wrote is never silently destroyed.

## Step 5 — Write `.claude/handbook/profile.yml`

Read `${CLAUDE_PLUGIN_ROOT}/skills/enduser-handbook/assets/handbook.profile.example.yml` fresh
(do not work from a memorized key list) and produce a copy that keeps **every key and comment**
from the canonical example — this is a key-set-identity guarantee, not a byte-for-byte one.
Overwrite only the values determined in Step 2/Step 3; leave everything else at the documented
default with a `TODO:` marker per the list at the end of Step 3. Write the result respecting
the clobber-safety outcome from Step 4.

## Step 6 — Write `.claude/handbook/style-guide.md`

Read `${CLAUDE_PLUGIN_ROOT}/skills/enduser-handbook/assets/style-guide.example.md` fresh and
write a copy to `.claude/handbook/style-guide.md`, pre-filling the language, register, and
persona fields from Step 2's answers and leaving the remaining dimensions as `TODO:` for the
user to complete. Respect the clobber-safety outcome from Step 4. This file must exist or the
skill's Step 0a halts on `style_guide.source path not found`.

## Step 7 — Run the Step 0 / Step 0a / Step 0b existence gates inline

Run the same three checks the skill's own `Step 0`, `Step 0a`, and `Step 0b` perform, sourced
from `SKILL.md` so they cannot drift out of sync with it — but only the **existence** checks,
never a live capture:

1. **Step 0** — `profile_version` in the file you just wrote is a version the skill supports.
2. **Step 0a** — the `style_guide.source` file exists (it does, because Step 6 just wrote it).
3. **Step 0b** — the publish-target adapter file resolves: lowercase `publish.target` and
   replace underscores with hyphens (`obsidian_vault` → `obsidian-vault.md`, `static_md` →
   `static-md.md`), then confirm
   `${CLAUDE_PLUGIN_ROOT}/skills/enduser-handbook/references/publish-targets/<resolved-name>.md`
   exists.

Report the outcome of all three gates to the user.

## Step 8 — Recommend next steps, then stop

Summarize what you wrote (including every field left at `TODO:`), then recommend — but do not
invoke — a follow-up validation pass: tell the user to run the enduser-handbook skill's own
`Step 0` (which re-checks the full profile) as a separate, human-initiated step, or a dedicated
profile validator if this plugin ships one. Do not name a guessed filename for a validator you
have not confirmed exists. You do not call the `Skill` tool and you do not run `capture.command`
from this command — driving the live application is a deliberate, separate action the user
takes afterward.
