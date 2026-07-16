# Publish target: Obsidian vault

You read this file when `publish.target: obsidian_vault`. It is the Obsidian-specific
publish-target adapter (the `static_md` adapter publishes to a plain-Markdown docs tree
instead). Every path here resolves through profile keys — never hardcode a project layout. Obsidian-specific names (Dataview, wikilinks, INDEX.md, `.md` frontmatter)
are deliberate: that is this adapter's job.

## What "Obsidian vault" implies

The publish destination is a folder tree of plain Markdown files inside an Obsidian vault.
You can rely on three Obsidian-specific features:

- **Wikilinks** — `[[path/to/note|display text]]`. Enabled when `publish.wikilinks: true`.
  When `false`, fall back to standard Markdown links and skip the wikilink-specific
  steps below (Related block, glossary linking syntax).
- **Dataview** — code-fenced ` ```dataview ` queries that render as live tables/lists.
  Only emit Dataview if the vault already uses it; do not introduce it unprompted.
- **INDEX.md convention** — each top-level vault section has an `INDEX.md` that tracks
  status rows for its sub-sections.

You do not own the vault. The user may already have a custom layout, Dataview dashboards,
templater scripts, and graph-view conventions. Add to it; never restructure it.

## Layout you produce

Resolve every path from profile keys. The shape below is the discipline; the literal
folder names come from the profile.

```
{{publish.chapters_dir}}/
  {{publish.index_file basename}}                # the section TOC, e.g. INDEX.md
  <chapter-slug>.md                              # one chapter per feature; slug is English kebab-case
{{capture.output_dir}}/<chapter-slug>/NN-*.png   # screenshots captured for this chapter; resolves to
                                                  # assets/<chapter-slug>/ for the example profile below
{{publish.glossary_dir}}/
  index.md                                       # canonical glossary page (see glossary-discipline.md)
{{publish.glossary_seed}}                        # vault-wide INDEX that tracks the glossary row
```

Chapter slugs are **always English kebab-case** even when the prose is in another
language. The H1 and body render in `language.code`; only the filename and the URL-ish
slug stay English. This keeps the file tree greppable and the wikilink targets stable
across translations.

Screenshots are captured into `{{capture.output_dir}}/<chapter-slug>/` and embedded by a
**full-target relative path** — never a raw `capture.output_dir` value and never a
partial concatenation of a chapter→output_dir prefix with the slug and filename:

```
<embed> = relative(dirname(chapter_file), join(capture.output_dir, <chapter-slug>, <file>))
```

Embed it as `![alt](<embed>)`. Two worked examples:

- `capture.output_dir: vault/handbook/assets`, chapter in `vault/handbook/` →
  `![alt](assets/<chapter-slug>/01-overview.png)`.
- **Flat** `capture.output_dir: vault/handbook` (same directory as the chapters), chapter
  in `vault/handbook/` → `![alt](<chapter-slug>/01-overview.png)` — no leading slash.
  (The naive `<rel>/<chapter-slug>/<file>` concatenation degenerates here: `<rel>` is
  empty, so it would wrongly produce a forbidden vault-rooted `/<chapter-slug>/…` path —
  always derive the embed from the full join above, never by concatenating a separately
  computed chapter→output_dir relative prefix with the slug and filename.)

The resulting embed must always be a **POSIX forward-slash** relative path — never
absolute, never `vault/`-rooted. If `relative(...)` on your platform would emit
backslashes or an absolute/cross-root path, normalize separators to `/` by hand, and keep
`capture.output_dir` on the same filesystem root as the vault so a relative path always
exists. Obsidian resolves relative paths and the chapter stays portable if the vault is
renamed.

## Frontmatter

When `publish.frontmatter_required: true`, every chapter starts with YAML frontmatter:

```
---
type: handbook
section: handbook
date: YYYY-MM-DD
status: active
language: {{language.code}}
tags: [handbook, <area>]
---
```

`<area>` is a one-word topical tag the chapter author picks (e.g. `auth`, `billing`).
`language` is non-negotiable when the profile sets it — downstream Dataview queries and
multi-language vaults filter on it. If `publish.frontmatter_required: false`, you may
omit the block but you still set `language` somewhere queryable (e.g. an inline tag).

## Chapter structure (Obsidian-flavoured)

The Diátaxis-driven skeleton lives in `references/diataxis.md`. Two Obsidian-specific
mechanics matter at publish time:

- **Section labels are profile-driven.** The `## Voraussetzungen` / `## Verwandte Themen`
  H2s render as `## {{publish.section_labels.prerequisites}}` and
  `## {{publish.section_labels.related}}` — literal strings the user wrote in their
  language. Do not translate them yourself.
- **The Related block ends every chapter** and contains ≥2 wikilinks to sibling chapters
  or glossary entries: `- [[<chapter-slug>|Display text]]`. This is what makes the
  Obsidian graph view useful; a chapter with no outbound wikilinks is a graph island
  and you halt the publish step until at least two exist.

Start from `assets/chapter-template.md` and substitute the placeholders — never
hand-rewrite the skeleton from memory.

## INDEX wiring (do all of these on every chapter create/update)

These are the Obsidian-specific writes that turn a new `.md` file into a discoverable
chapter. Skip any of them and the chapter exists but no reader will find it.

1. **`{{publish.index_file}}`** — the section TOC. Add a row for the new chapter under
   the appropriate heading. Order alphabetically by display title unless the existing
   file uses a different order — match what is there.

2. **`{{publish.glossary_seed}}`** — the vault-wide INDEX that tracks section status.
   Confirm there is a `handbook` row with status `active` listing the section. If the
   row is missing, add it. If it exists with status `seed`, flip to `active` once your
   first real chapter lands.

3. **Dashboard / graph entry points** (only if the vault has one). Many vaults use a
   `Dashboard.md` with Dataview blocks scoped to a folder. The pattern is:
   ```dataview
   table status, date
   from "{{publish.chapters_dir basename}}"
   where type = "handbook"
   sort date desc
   ```
   If a dashboard already exists scoped to a sibling folder (e.g. `from "knowledge"`),
   the handbook will **not** appear there automatically — you add a second Dataview
   block scoped to the chapters folder, plus a manual nav link. Do not touch the
   existing block; append.

4. **Vault log** (optional but common: `{{publish.chapters_dir}}/../knowledge/log.md` or
   similar). When the vault keeps a chronological change log, append
   `## [YYYY-MM-DD] create | handbook: <chapter-slug>` for new chapters,
   `update` for revisions. Read the existing log first to copy its verb vocabulary and
   heading depth — projects diverge here.

5. **`CLAUDE.md`** — keep one short line near the vault map noting
   `{{publish.chapters_dir}}/ is the end-user handbook section`. This is what tells
   future Claude Code sessions (and the `obsidian-project-vault` skill if installed)
   that the directory is owned by this skill and not by general note-taking.

## Wikilinks vs Markdown links

`publish.wikilinks: true` (Obsidian default):

- Internal chapter link: `[[<chapter-slug>|Display title]]`
- Glossary link: `[[{{publish.glossary_dir}}/index#Term|Term]]`
- The pipe `|` separates target from display; omit it when display equals target.

`publish.wikilinks: false`:

- Internal chapter link: `[Display title](<chapter-slug>.md)`
- Glossary link: `[Term]({{publish.glossary_dir}}/index.md#term)` (lowercase, hyphenated
  anchor — GitHub Markdown convention).
- Skip Dataview blocks; they require Obsidian to render.

You do not mix the two styles in one chapter. The profile decides; the chapter follows.

## Glossary backlink discipline

Every domain term's **first occurrence** in a chapter links to its glossary entry. The
glossary itself lives at `{{publish.glossary_dir}}/index.md` and is owned by
`references/glossary-discipline.md` — this adapter only encodes the linking syntax:

- Wikilinks on: `[[{{publish.glossary_dir basename}}/index#TermHeading|TermHeading]]`
- Wikilinks off: `[TermHeading]({{publish.glossary_dir}}/index.md#termheading)`

The glossary entry heading is the term in `glossary.canonical_term_language`; the
English code identifier is a field inside the entry, not the heading.

## Link integrity gate before you publish

Before declaring the chapter published, you verify in this order and halt on the first
failure:

1. Every `![](…)` embed, resolved **relative to the chapter that contains it**, points
   at a PNG that actually exists under `{{capture.output_dir}}/<chapter-slug>/` — no
   orphan embeds, no captures the run did not produce. The resolved target must also
   stay inside the active Obsidian vault — halt if `capture.output_dir` resolves outside
   the vault root (e.g. `capture.output_dir: screenshots` from a chapter at
   `vault/handbook/foo.md` resolves to `../../screenshots/…`, outside the vault, so the
   embed is broken and unportable). Unlike the static-Markdown target,
   `capture.output_dir` is **not** required to sit under `publish.chapters_dir` — sibling
   vault subtrees resolve fine as long as the target stays inside the vault.
2. Every wikilink target (`[[…]]`) resolves to either an existing `.md` file in the
   vault or an existing heading anchor in the glossary. Broken wikilinks render as
   red placeholders in Obsidian and are silent in plain Markdown views.
3. The chapter has ≥2 outbound links in its Related block (graph-island check).
4. The frontmatter `language` matches `language.code`; the section labels match
   `publish.section_labels.*` verbatim.
5. `{{publish.index_file}}` lists the chapter; `{{publish.glossary_seed}}` has the
   `handbook` row marked `active`.

A chapter that fails any of these is unpublished, not "almost done" — fix and re-verify.
