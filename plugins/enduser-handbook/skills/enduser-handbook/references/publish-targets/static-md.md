# Publish target: static Markdown

You read this file when `publish.target: static_md`. It is the universal plain-Markdown
fallback adapter: it publishes the handbook to any docs tree that renders ordinary Markdown
files — a GitHub wiki, an MkDocs/GitBook/Docusaurus source tree, or a plain repository
folder. Every path here resolves through profile keys — never hardcode a project layout, and
never bake a raw `publish.*` path value into a link.

## What "static Markdown" implies

The publish destination is a folder tree of plain Markdown files that some renderer turns
into a static site (or that readers browse directly on a code host). You can rely on **none**
of the Obsidian-specific features:

- **No graph view, no backlinks panel.** Navigability comes from links you write, not from a
  graph the tool computes. Every chapter must link back to the index so a reader can find it.
- **No Dataview.** Static renderers cannot execute Obsidian Dataview query blocks — they
  render as a raw code block. You never emit a Dataview block on this target.
- **Standard Markdown links only — not Obsidian wikilinks.** A static renderer does not
  resolve double-bracket wikilink syntax; it prints it literally. This adapter therefore
  **requires** `publish.wikilinks: false` and halts unless the profile sets it explicitly false
  (see "Halt conditions"). The index is a flat table of contents, not an Obsidian `INDEX.md`
  with status rows.

You do not own the docs tree. The user may already have an MkDocs nav, a GitBook
`SUMMARY.md`, or a hand-curated wiki sidebar. Add to it; never restructure it.

## Layout you produce

Resolve every path from profile keys. The shape below is the discipline; the literal folder
names come from the profile.

```
{{publish.chapters_dir}}/
  <chapter-slug>.md                              # one chapter per feature; slug is English kebab-case
{{capture.output_dir}}/<chapter-slug>/NN-*.png   # screenshots, retained here (NOT copied); MUST resolve under chapters_dir — see Assets
{{publish.glossary_dir}}/
  index.md                                       # canonical glossary page (see glossary-discipline.md)
{{publish.index_file}}                           # the flat table of contents (e.g. SUMMARY.md / README.md)
```

Chapter slugs are **always English kebab-case** even when the prose is in another language.
The H1 and body render in `language.code`; only the filename and the URL-ish slug stay
English. This keeps the file tree greppable and the link targets stable across translations.

## Assets

Screenshots are captured into `{{capture.output_dir}}/<chapter-slug>/` and **remain there** — the
base skill does not copy assets into the chapters tree (`capture.output_dir` is the single retained
location; see `SKILL.md` W5, "Assets remain at `capture.output_dir`"). Embed each by a **relative
path from the chapter to that retained location**: `![alt](<rel>/<chapter-slug>/01-overview.png)`,
where `<rel>` is `relative(dirname(chapter_file), capture.output_dir)`. For the example layout
(`capture.output_dir: vault/handbook/assets`, chapter in `vault/handbook/`) that resolves to
`![alt](assets/<chapter-slug>/01-overview.png)`. Never absolute, never docs-root-rooted paths.

A static renderer serves only files **inside** the published docs tree, so `capture.output_dir`
MUST resolve under `publish.chapters_dir` (point it at e.g. `<chapters_dir>/assets`) — otherwise the
embed resolves to a file outside the served tree and the image 404s while the rest of the page
renders. This is a halt condition (below), and the link-integrity gate re-checks it per embed.

## Frontmatter

Honor `publish.frontmatter_required`.

When `publish.frontmatter_required: true`, every chapter starts with a **minimal** standard
YAML frontmatter block — only keys a generic static generator understands:

```
---
title: <chapter display title>
date: YYYY-MM-DD
language: {{language.code}}
---
```

Keep it minimal on purpose: MkDocs, GitBook, and Docusaurus each reject or warn on unknown
frontmatter keys, so do **not** carry the Obsidian-flavoured `type`/`section`/`status`/`tags`
block here — nor the authoring-only `glossary_terms` list (a manifest/authoring field, see
"Glossary backlink discipline"), which is never emitted into the published frontmatter.
`language` stays in when the profile sets it. When
`publish.frontmatter_required: false`, omit the block entirely — a plain wiki or
`SUMMARY.md`-only tree often has no frontmatter convention, and an injected block would render
as visible text at the top of the page.

## Chapter structure

The Diátaxis-driven skeleton lives in `references/diataxis.md`. Start from
`assets/chapter-template.md` and substitute the placeholders — never hand-rewrite the skeleton
from memory. Two mechanics matter at publish time for this target:

- **Section labels are profile-driven.** The prerequisites and related H2s render as
  `## {{publish.section_labels.prerequisites}}` and `## {{publish.section_labels.related}}` —
  literal strings the user wrote in their language. Do not translate them yourself.
- **The Related block ends every chapter** and renders as plain Markdown links, the way the
  Obsidian-default template's placeholders are overridden for a static target. Use standard
  Markdown links, not Obsidian wikilinks. Each line is one of three forms:
  - `- [Title](slug.md)` — a sibling chapter;
  - `- [Term](<glossary-rel>/index.md#term)` — a glossary entry (see "Glossary backlink
    discipline" below for `<glossary-rel>`);
  - `- [<index label>](<relative-index-path>)` — the index, e.g. `- [All chapters](../SUMMARY.md)`.
  At least one line resolves to the index (see the gate below) so the chapter is reachable.

## Relative links — the general rule

Chapters live under `publish.chapters_dir`; the glossary lives under `publish.glossary_dir` (a
different subtree); the index lives at `publish.index_file` (often a different subtree again).
A link baked from a raw profile key value — e.g. `[Term](vault/knowledge/glossary/index.md#term)`
— breaks in every rendered tree, because the renderer resolves links relative to the **source
file**, not the repo root.

So compute every link relative to the chapter that contains it:

```
relative(dirname(chapter_file), target_file)
```

The nesting depth of `chapters_dir` varies the number of `../` segments, and the formula always
yields the right path. The literal paths below are **examples for this layout** — never copy a
literal across to a profile with a different layout; re-derive it from the formula.

- **Chapter → glossary** (example for this layout, with `chapters_dir: vault/handbook` and
  `glossary_dir: vault/knowledge/glossary`): `[Term](../knowledge/glossary/index.md#term)` —
  one `../` to climb out of `handbook/`, then down into the sibling `knowledge/glossary` subtree.
  The anchor is the lowercased, hyphenated term (GitHub Markdown convention).
- **Chapter → sibling chapter** (example for this layout): `[Title](other-slug.md)` — both files
  live in `chapters_dir`, so the link is the bare filename with no `../`.
- **Chapter → index** depends on where `index_file` sits relative to the chapter:
  - **vault-root index** (example for this layout, `index_file: vault/SUMMARY.md`, chapter in
    `vault/handbook/`): `[All chapters](../SUMMARY.md)` — one `../`.
  - **repo-root index** (example for this layout, `index_file: SUMMARY.md`, chapter in
    `vault/handbook/`): `[All chapters](../../SUMMARY.md)` — two `../`.

## Index wiring (do this on every chapter create/update)

Static-target index wiring is deliberately simpler than the Obsidian path — there is **no**
Dataview dashboard, **no** `log.md`, and **no** `CLAUDE.md` vault-map line. There are **two
required writes**, plus one conditional `publish.glossary_seed` reconciliation:

1. **`{{publish.index_file}}`** — the flat table of contents (`SUMMARY.md`, `README.md`, an
   MkDocs `nav:` list, etc.). Add **one** TOC line linking to the new chapter, computed
   relative to the index file's own directory. Order alphabetically by display title unless the
   existing file uses a different order — match what is there. Do not rewrite unrelated rows.
2. **Glossary entry** — for each new domain term, add or link its entry under
   `{{publish.glossary_dir}}/index.md` (the page is owned by `references/glossary-discipline.md`;
   this adapter only encodes the relative link syntax).
3. **`{{publish.glossary_seed}}` reconciliation (conditional)** — only when `publish.glossary_seed`
   is set and readable, reconcile its row as that file's convention requires; when it is unset,
   proceed without it — a static docs tree often has no seed index.

## Glossary backlink discipline

Every domain term's **first occurrence** in a chapter links to its glossary entry with a
relative Markdown link: `[TermHeading](<glossary-rel>/index.md#termheading)`, where
`<glossary-rel>` is `relative(dirname(chapter_file), publish.glossary_dir)` (for the example
layout above, `<glossary-rel>` resolves to `../knowledge/glossary`, so the link is
`[TermHeading](../knowledge/glossary/index.md#termheading)`). The anchor is lowercased and
hyphenated. The glossary entry heading is the term in `glossary.canonical_term_language`; the
English code identifier is a field inside the entry, not the heading. The term set comes from
the manifest `glossary_terms` list — the authoring source of truth, kept in sync with the
chapter's authoring frontmatter per `manifest-discipline.md` and populated from
`publish.glossary_seed` when set. That field is authoring-time only; the minimal published
frontmatter (see "Frontmatter") does not carry it. Use the canonical term, never a camelCase
variant.

## Halt conditions

Before you write a single chapter file, verify and **halt** on the first failure — do not
produce a partial tree:

1. **`publish.index_file` is set and writable** — the file itself if it already exists (index
   wiring appends a TOC line to it), or its parent directory if the file is absent and must be
   created. A static handbook with no reachable, writable index is an island of orphan pages;
   refuse to publish without one. Halt with: "static_md requires `publish.index_file` to point at
   a writable table of contents — set it and ensure the file (or its parent directory, if the file
   does not yet exist) is writable before publishing."
2. **`publish.chapters_dir` is writable.** You cannot place chapters otherwise. Halt with:
   "static_md cannot write chapters — `publish.chapters_dir` is unset or not writable."
3. **The glossary target is writable.** Index wiring adds or links a glossary entry under
   `publish.glossary_dir/index.md` (and, when `publish.glossary_seed` is set, reconciles its row),
   so that file must be writable if it exists or creatable if absent, and the seed must be writable
   when reconciliation applies. An unwritable target leaves a missing or broken glossary backlink
   silently. Halt with: "static_md cannot write the glossary — `publish.glossary_dir`/index.md (or
   `publish.glossary_seed`, when set) is not writable or creatable."
4. **`capture.output_dir` resolves under `publish.chapters_dir`.** A static renderer serves only
   files inside the published docs tree, so the retained screenshots must live within it. Halt with:
   "static_md requires `capture.output_dir` to resolve under `publish.chapters_dir` so the rendered
   site can serve screenshots — point it inside the docs tree (e.g. `<chapters_dir>/assets`) and
   re-run."
5. **`publish.wikilinks` is explicitly `false`.** This target cannot render Obsidian wikilinks, and
   an unset value would fall back to Obsidian's wikilinks-on default and silently break every
   relative link. If a `static_md` profile sets `wikilinks: true` **or leaves it unset**, halt with:
   "static_md requires `wikilinks: false` — Obsidian wikilinks do not render on a static site; set
   `publish.wikilinks: false` in the profile and re-run." Never silently emit plain links over a
   `wikilinks: true` (or unset) profile — the profile and the output must agree.
6. **No network.** This adapter is file-only. If publishing would require an HTTP call, an API
   token, or auth (a hosted Confluence/GitBook API), that is a different target. Halt with:
   "static_md writes local files only — a hosted Confluence/GitBook API target needs a different
   `publish.target` adapter."

## Link-integrity gate before you publish

Before declaring the chapter published, you verify in this order and halt on the first failure:

1. Every `![](…)` embed, resolved relative to the chapter, points at a PNG that actually exists
   under `{{capture.output_dir}}/<chapter-slug>/` (the retained location), AND that location
   resolves under `{{publish.chapters_dir}}` so the static site can serve it — no orphan embeds, no
   captures the run did not produce, no embed pointing outside the published tree.
2. Every relative Markdown link resolves to a real file (and, for glossary links, a real heading
   anchor). Compute each from `relative(dirname(chapter_file), target_file)` and confirm the
   target exists. Broken relative links 404 on a static site and are silent in raw views.
3. The chapter has **at least one** link back to `{{publish.index_file}}` (navigability check).
   Unlike Obsidian, there is no graph view, so a missing second sibling link does **not** halt —
   one resolved index link is the minimum bar.
4. The frontmatter `language` (when frontmatter is required) matches `language.code`; the section
   labels match `publish.section_labels.prerequisites` and `publish.section_labels.related`
   verbatim.
5. `{{publish.index_file}}` lists the chapter with a link that resolves from the index's own
   directory.

A chapter that fails any of these is unpublished, not "almost done" — fix and re-verify.
