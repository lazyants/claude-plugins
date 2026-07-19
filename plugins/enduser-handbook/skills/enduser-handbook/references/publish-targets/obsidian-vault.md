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
  <chapter-slug>.md                              # flat chapter (entry has no `group`)
  <group>/<chapter-slug>.md                      # grouped chapter (entry sets `group`)
{{capture.output_dir}}/<chapter-slug>/NN-*.png          # flat chapter's screenshots
{{capture.output_dir}}/<group>/<chapter-slug>/NN-*.png  # grouped chapter's screenshots
{{publish.glossary_dir}}/
  index.md                                       # canonical glossary page (see glossary-discipline.md)
{{publish.glossary_seed}}                        # vault-wide INDEX that tracks the glossary row
```

Chapter slugs are **always English kebab-case** even when the prose is in another
language. The H1 and body render in `language.code`; only the filename and the URL-ish
slug stay English. This keeps the file tree greppable and the wikilink targets stable
across translations.

`group` is an optional field on a manifest entry (`references/manifest-discipline.md`),
also always English kebab-case, one level (no `/`). A manifest where no entry sets it —
the 1.4.1 shipped default — produces only the flat form above. As of 1.6.0, the two
exceptions are in `assets/lib/chapter-paths.mjs` — `staticEmbedPath` (the asset-embed
path formula, "Layout you produce" below, now always the full-target join) and
`validateGroups` (the duplicate-slug halt, always runs). In `publish.wikilinks: false`
mode this adapter ALSO applies the full-target glossary formula and runs the Markdown-link
integrity gate for group-free manifests (see "Glossary backlink discipline" and "Link
integrity gate before you publish" below). Every other section keeps the 1.4.1 group-free
behavior unchanged. Flat and grouped entries coexist in one manifest. Canonical chapter
path (D2, shared with `static-md.md` and `SKILL.md`):

```
grouped: {{publish.chapters_dir}}/<group>/<slug>.md
flat:    {{publish.chapters_dir}}/<slug>.md
```

(`<slug>` above is this file's `<chapter-slug>` elsewhere.)

Screenshots are captured into that chapter's derived asset dir — `chapterAssetDir(entry)`
(D3) — and embedded by a **full-target relative path** — never a raw `capture.output_dir`
value and never a partial concatenation of a chapter→output_dir prefix with the slug and
filename:

```
chapterAssetDir(entry) = join(capture.output_dir, entry.group?, entry.slug)
<embed> = relative(dirname(chapter_file), join(chapterAssetDir(entry), <file>))
```

`entry.group?` means the group segment is present only for a grouped entry; a flat entry
(no `group`) collapses this back to `join(capture.output_dir, entry.slug)` — byte-identical
to 1.4.1. Embed it as `![alt](<embed>)`. Three worked examples:

- `capture.output_dir: vault/handbook/assets`, chapter in `vault/handbook/` →
  `![alt](assets/<chapter-slug>/01-overview.png)`.
- **Flat** `capture.output_dir: vault/handbook` (same directory as the chapters), chapter
  in `vault/handbook/` → `![alt](<chapter-slug>/01-overview.png)` — no leading slash.
  (The naive `<rel>/<chapter-slug>/<file>` concatenation degenerates here: `<rel>` is
  empty, so it would wrongly produce a forbidden vault-rooted `/<chapter-slug>/…` path —
  always derive the embed from the full join above, never by concatenating a separately
  computed chapter→output_dir relative prefix with the slug and filename.)
- **Grouped** (`anyGroup` manifest) `capture.output_dir: vault/handbook/assets`, entry
  `group: billing`, chapter at `vault/handbook/billing/invoices.md` →
  `![alt](../assets/billing/invoices/01-overview.png)`. `chapterAssetDir` mirrors the
  group segment into the asset tree, so the chapter is now one level deeper than its
  asset dir's common ancestor and the embed climbs `../` to reach it — see "Grouped
  chapters and Quartz" below for a resolver caveat with this climb.

The resulting embed must always be a **POSIX forward-slash** relative path — never
absolute, never `vault/`-rooted. If `relative(...)` on your platform would emit
backslashes or an absolute/cross-root path, normalize separators to `/` by hand, and keep
`capture.output_dir` on the same filesystem root as the vault so a relative path always
exists. Obsidian resolves relative paths and the chapter stays portable if the vault is
renamed.

## Grouped chapters and Quartz

A grouped chapter's embed can climb one or more `../` segments to reach its asset dir
(the worked example above). That resolves correctly in Obsidian itself, in any renderer
that treats embeds as ordinary relative paths, and under Quartz's
`markdownLinkResolution: relative`. It does **not** resolve under Quartz's `shortest`
mode — the default most Quartz vaults run, since the `quartz create` Obsidian template
auto-selects it. Under `shortest`, Quartz resolves a link by matching a bare file name
or, failing that, a content-root-absolute path; a `../`-relative embed is neither, so it
renders broken. If this vault publishes through Quartz-`shortest`, either flip that
vault's config to `markdownLinkResolution: relative` — a per-vault tradeoff the adopter
owns, since it can also change how bare wikilinks resolve elsewhere in the vault — or
wait for the co-located-assets follow-up issue, which keeps a grouped chapter's assets in
the same directory as the chapter so no `../` climb is ever needed. This is documentation
only: the existing embed-exists and under-vault gates below are unaffected by depth, so
there is no new gate here.

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

1. **`{{publish.index_file}}`** — the section TOC. What "wire the chapter" means depends
   on whether the manifest entry sets `group` (`references/manifest-discipline.md`).

   **Flat entries** (no `group`, the 1.4.1 shipped case) — add a row for the new chapter
   under the appropriate heading. Order alphabetically by display title unless the
   existing file uses a different order — match what is there.

   **Grouped entries** (`anyGroup` manifests) additionally resolve a container, so wiring
   runs a fixed sequence every time — first run and re-run alike:

   - **Step 0 — idempotency check.** Compute the expected link target: for standard
     Markdown links (`publish.wikilinks: false`), `relative(dirname(index_file), chapter_file)`;
     for wikilinks (the Obsidian default), the bare chapter slug — safe to match on
     because slugs stay globally unique across every group. Scan `{{publish.index_file}}`
     for a line matching that target — `locateChapterLine` (`assets/lib/chapter-paths.mjs`)
     returns the match plus a structural `indexForm: 'headings' | 'non-heading'` field; key
     every branch below on `indexForm`, never on whether `containerTitle` is `null` — a
     `null` title occurs both for a genuinely non-heading file and for an uncontained match
     inside a headings-form file, and those two cases need different handling, below.
     - The target matches more than one line — never guess which line is canonical, halt:
       "Chapter '<slug>' appears multiple times in <index_file> — curate the index manually, then re-run."
     - `indexForm === 'headings'` and the matching line sits under a heading matching the
       entry's current `group_title` — compare via
       `containerTitleMatches(containerTitle, entry)` (titles compare TRIMMED, so a padded
       manifest title still converges) — for this chapter, wiring is already complete; go
       straight to the link-integrity gate below.
     - `indexForm === 'headings'` and the matching line sits under a **different** heading
       — never silently relocate a user-curated line, halt:
       "Chapter '<slug>' is listed in <index_file> under '<found_title>' instead of '<group_title>' — move the line (or curate the index manually), then re-run."
     - `indexForm === 'headings'` and the matching line is **uncontained**
       (`containerTitle` is `null` — the line sits above the file's first `##`, or after an
       H1 that resets the active container) — this is a misplaced line, not a completed
       one: apply the same halt as the previous bullet, with `<found_title>` read as
       "(none)" in the halted message.
     - `indexForm === 'non-heading'` (a nested list, an MkDocs-style YAML `nav:`, a bare
       path table, …) and the target matches anywhere — wiring is already complete.
     - No match in either form — continue to container resolution.
   - **Container resolution** (headings-form index only — the only automated grouped form
     in 1.5.0; every other index shape is fully manual, next bullet). Look for a heading
     whose text equals the entry's `group_title` — containers are located by title, never
     by the English `group` slug:
     - Zero matches — create one (`## <group_title>`, at the heading depth the file
       already uses for its top-level sections), then append the chapter line under it.
     - Exactly one — append the chapter line under it, respecting whatever ordering
       convention the file already follows.
     - More than one — halt:
       "Found multiple '<group_title>' containers in <index_file> — curate the index manually, then re-run."
   - **Non-headings index, no existing line.** This is fully manual in 1.5.0. Halt:
     "Index <index_file> is not a headings-form file — add a '<group_title>' container and the chapter line for '<slug>' manually, then re-run."
     The next run's step 0 finds the line you added and proceeds — this convergence is
     why step 0 always runs first.

   **Manual group migration is a different halt, not part of establishment.** A manifest
   edit that changes a retained entry's `group` or `group_title`, or removes a grouped
   entry, is never handled by the flow above — it halts with
   `This manifest change requires manual group migration (not automated in 1.5.0):`
   and the recipe in `references/revalidation.md` (see `SKILL.md` W6). Step 0 above only
   ever finds or adds a line; it never moves or deletes one.

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
- Wikilinks resolve by basename, so grouping never changes this form — a chapter slug
  stays globally unique across every group (`references/manifest-discipline.md`), so no
  relative-path math is ever needed here, grouped or flat.

`publish.wikilinks: false`:

- Internal chapter link, group-free manifest (shipped 1.4.1 form, unchanged):
  `[Display title](<chapter-slug>.md)`.
- Internal chapter link, `anyGroup` manifest — every chapter the skill WRITES (new
  chapters, and chapters a manual-migration rewrite touches) uses the full-target formula
  instead of the bare-filename form above, because the linking and target chapters can now
  sit in different groups (write-time canon; retained chapters keep whatever spelling they
  already have — the link-integrity gate below checks that the target resolves, not that
  the spelling matches this formula): `[Display title](relative(dirname(chapter_file), <target-chapter-file>))`.
- Glossary link: see "Glossary backlink discipline" below.
- Skip Dataview blocks; they require Obsidian to render.

You do not mix the two styles in one chapter. The profile decides; the chapter follows.

## Glossary backlink discipline

Every domain term's **first occurrence** in a chapter links to its glossary entry. The
glossary itself lives at `{{publish.glossary_dir}}/index.md` and is owned by
`references/glossary-discipline.md` — this adapter only encodes the linking syntax:

- Wikilinks on: `[[{{publish.glossary_dir basename}}/index#TermHeading|TermHeading]]`
- Wikilinks off, any manifest — every chapter the skill WRITES uses the full-target
  formula (write-time canon; retained chapters keep whatever spelling they already have,
  per the link-integrity gate below):
  `[TermHeading](relative(dirname(chapter_file), {{publish.glossary_dir}}/index.md)#termheading)`.

The glossary entry heading is the term in `glossary.canonical_term_language`; the
English code identifier is a field inside the entry, not the heading.

## Link integrity gate before you publish

Before declaring the chapter published, you verify in this order and halt on the first
failure:

1. Every `![](…)` embed, resolved **relative to the chapter that contains it**, points
   at a PNG that actually exists under that chapter's derived asset dir —
   `{{capture.output_dir}}/<chapter-slug>/` for a flat entry,
   `{{capture.output_dir}}/<group>/<chapter-slug>/` for a grouped one
   (`chapterAssetDir(entry)`, D3) — no orphan embeds, no captures the run did not produce.
   The resolved target must also stay inside the active Obsidian vault — halt if
   `capture.output_dir` resolves outside the vault root (e.g.
   `capture.output_dir: screenshots` from a chapter at `vault/handbook/foo.md` resolves
   to `../../screenshots/…`, outside the vault, so the embed is broken and unportable). This
   containment check applies unchanged at any group depth. Unlike the static-Markdown
   target, `capture.output_dir` is **not** required to sit under `publish.chapters_dir` —
   sibling vault subtrees resolve fine as long as the target stays inside the vault.
2. Every wikilink target (`[[…]]`) resolves to either an existing `.md` file in the
   vault or an existing heading anchor in the glossary. Broken wikilinks render as
   red placeholders in Obsidian and are silent in plain Markdown views. When
   `publish.wikilinks: false`, this item also verifies every standard Markdown link
   (`[text](target)`) resolves the same way — every manifest, group-free manifests included:
   grouped chapters can sit at different depths, so a stale or hand-edited relative link
   is exactly as broken as a dangling wikilink and must be caught here too.
   **This gate is chapter-scoped**: it fires here, before declaring the chapter
   published, so it catches a legacy broken link only when that chapter is next
   published, or revalidated in a way that **touches** it — an accepted-diff refresh
   or a material re-author (`references/revalidation.md`). A **no-op** revalidation
   classifies the chapter unchanged and never runs this gate. It does not sweep untouched chapters
   — an already-published chapter with a stale link stays broken until a publish, or a touching
   revalidation, next runs against it.
3. The chapter has ≥2 outbound links in its Related block (graph-island check).
4. The frontmatter `language` matches `language.code`; the section labels match
   `publish.section_labels.*` verbatim.
5. `{{publish.index_file}}` lists the chapter — under its `group_title` container, for a
   grouped entry (per "INDEX wiring" above); `{{publish.glossary_seed}}` has the
   `handbook` row marked `active`.

A chapter that fails any of these is unpublished, not "almost done" — fix and re-verify.
