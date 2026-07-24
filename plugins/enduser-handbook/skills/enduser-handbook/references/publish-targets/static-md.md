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
  <chapter-slug>.md                              # flat entry (no group); slug is English kebab-case
  <group>/<slug>.md                              # grouped entry; group is English kebab-case, one level
{{capture.output_dir}}/<chapter-slug>/NN-*.png   # flat entry's screenshots (NOT copied); MUST resolve under chapters_dir
{{capture.output_dir}}/<group>/<slug>/NN-*.png   # grouped entry's screenshots; same containment rule — see Assets
{{publish.glossary_dir}}/
  index.md                                       # canonical glossary page (see glossary-discipline.md)
{{publish.index_file}}                           # the flat table of contents (e.g. SUMMARY.md / README.md)
```

Chapter slugs are **always English kebab-case** even when the prose is in another language.
The H1 and body render in `language.code`; only the filename and the URL-ish slug stay
English. This keeps the file tree greppable and the link targets stable across translations.

**Chapter path.** `group` set on the manifest entry ⇒ `publish.chapters_dir/<group>/<slug>.md`;
`group` unset ⇒ `publish.chapters_dir/<slug>.md` — the shipped 1.4.1 form, unchanged. `group` is
English kebab-case, one level (nested groups like `a/b` are out of scope for 1.5.0).

**Activation rule.** This adapter's group-aware machinery — the grouped branch of the chapter
path above, and the grouped index-wiring container logic further down — is gated on
`anyGroup(entries)`. The grouped-**path** half is pinned by unit test: a wholly group-free
manifest never produces a grouped chapter path. The grouped-**index-line** half is **not**
independently pinned — no code in this repo emits index lines at all, so no direct
`anyGroup(...) === false` assertion exists to run one; both mutation directions are exercised
only transitively, through whatever wiring behavior consumes `anyGroup`.
`assets/lib/chapter-paths.mjs`'s own activation rule has **two 1.6.0 exceptions that are
group-free-aware by design and no longer consult `anyGroup`: `staticEmbedPath` (see "Assets"
below) and `validateGroups` (see `manifest-discipline.md`)**. That count is a property of the
**helper module**, not a ceiling on adapter behavior — an individual publish-target adapter
(this one, or another) may carry its own group-free behavior changes on top of it, so
`anyGroup` gating must never be assumed to cover everything an adapter does.

## Assets

Screenshots are captured into the entry's asset dir and **remain there** — the base skill does
not copy assets into the chapters tree (`capture.output_dir` is the single retained location; see
`SKILL.md` W5, "Assets remain at `capture.output_dir`"). The asset dir is:

```
chapterAssetDir(entry) = join(capture.output_dir, entry.group?, entry.slug)
```

— `{{capture.output_dir}}/<chapter-slug>/` for a flat entry, `{{capture.output_dir}}/<group>/<slug>/`
for a grouped one.

**The write canon is unconditional: flat entries and group-free manifests alike use the same
full-target embed formula as grouped ones** — there is no group-free branch left in this adapter's
embed spelling. Every chapter this skill writes computes:

```
<embed> = relative(dirname(chapter_file), join(chapterAssetDir(entry), <file>))
```

Embed it as `![alt](<embed>)`. Do **not** merely splice a `<group>/` segment into the superseded
`<rel>/<chapter-slug>/<file>` concatenation (`<rel>` = `relative(dirname(chapter_file),
capture.output_dir)`) — re-derive the whole path from `chapterAssetDir(entry)`. That superseded
concatenation and the full-target canon diverge outside the simplest layout — verified across the
three layouts that matter:

- **sibling** — `capture.output_dir` sits strictly below `publish.chapters_dir` (the common worked
  example above);
- **degenerate** — the chapter's own directory equals `capture.output_dir`;
- **parent** — `capture.output_dir` sits strictly above `publish.chapters_dir`, i.e. the chapter
  lives nested inside it.

| Layout     | Superseded concatenation | Full-target canon | Changes? |
|---|---|---|---|
| sibling    | `assets/items/01.png`    | same               | SAME     |
| degenerate | `/items/01.png`          | `items/01.png`     | CHANGES  |
| parent     | `../items/01.png`        | `01.png`           | CHANGES  |

`chapterAssetDir(entry)` resolves correctly in every layout whose operands share a common anchor,
degenerate ones included — the three rows above are **representative** of that class, pinned by
unit test, not an exhaustive enumeration of every possible directory topology. A **cousin**
topology, where `capture.output_dir` and the chapter's directory branch apart below a shared
ancestor rather than one nesting inside the other (e.g. `chapter_file: vault/docs/handbook/items.md`,
`capture.output_dir: vault/assets`), is not one of the three rows above but still agrees between
spellings — both resolve to `../../assets/items/01.png` (verified by running `embedPath` and
`legacyStaticEmbedPath` against those exact inputs). Divergence tracks the degenerate and parent
cases above specifically, not directory topology in general. Profile paths with unequal unresolved
leading `../` climbs (e.g. `chapter_file` and
`capture.output_dir` both expressed relative to a project root, but climbing out of it by a
different number of segments) are a known limitation of the shared `relative()` path helper in
`assets/lib/chapter-paths.mjs` (see #246) — pre-existing, not introduced or worsened by 1.6.0. It
produces the identical wrong result under both the superseded concatenation and the full-target
canon there, so neither spelling is more broken than the other on that class of profile path.

The superseded concatenation (`legacyStaticEmbedPath`) is retained in `assets/lib/chapter-paths.mjs`
only for exported-API compatibility — this adapter no longer calls it for any manifest, flat or
grouped.

Retained chapters keep whatever spelling already resolves — the link-integrity gate verifies
resolution, never spelling (see "Write-time canon" in `revalidation.md`) — so neither an `anyGroup`
flip nor this write-canon change, on its own, ever triggers a rewrite. 1.6.0 performs no automatic
retroactive repair of this change: a chapter is never rewritten *solely* because of an upgrade or
an `anyGroup` flip. Never absolute, never docs-root-rooted paths.

A static renderer serves only files **inside** the published docs tree, so `capture.output_dir`
MUST resolve under `publish.chapters_dir` (point it at e.g. `<chapters_dir>/assets`) — otherwise the
embed resolves to a file outside the served tree and the image 404s while the rest of the page
renders. This is a halt condition (below), which compares normalized resolved paths so it holds
for `chapterAssetDir(entry)` at any depth — flat or grouped — without change; the link-integrity
gate re-checks it per embed.

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
  - a sibling chapter, computed with the general relative-link formula from "Relative links — the
    general rule" below. **Group-free manifests, or two siblings in the same group**, produce the
    bare, same-directory spelling `- [Title](slug.md)` — this is what the formula naturally yields
    when both files share a directory, not a hardcoded special case — while **cross-group
    siblings** (`anyGroup`, different groups, or one chapter grouped and the other flat) never
    simplify that way, e.g. `- [Title](../billing/orders.md)`;
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

The nesting depth of `chapters_dir` varies the number of `../` segments, and the formula yields
the right path whenever `chapter_file` and `target_file` share a common anchor. It is the same
`relative()` helper the embed formula uses (see "Assets" above), so it carries the same known
limitation on profile paths with unequal unresolved leading `../` climbs. The literal paths below
are **examples for this layout** — never copy a literal across to a profile with a different
layout; re-derive it from the formula.

- **Chapter → glossary** (example for this layout, with `chapters_dir: vault/handbook` and
  `glossary_dir: vault/knowledge/glossary`): `[Term](../knowledge/glossary/index.md#term)` —
  one `../` to climb out of `handbook/`, then down into the sibling `knowledge/glossary` subtree.
  The anchor is the lowercased, hyphenated term (GitHub Markdown convention).
- **Chapter → sibling chapter**: apply the same formula above — `target_file` is the sibling's
  derived chapter path (see "Chapter path" above; flat or grouped), never assumed.
  - **Group-free manifest, or two siblings in the same group** (example for this layout):
    `[Title](other-slug.md)` — both files live in the same directory, so the link is the bare
    filename with no `../`.
  - **Cross-group siblings** (`anyGroup`, different groups — example: linking from
    `admin/items.md` to `billing/orders.md`): `[Title](../billing/orders.md)` — climb out of the
    current group directory, then back down into the target group's. Chapters share a directory
    only for a group-free manifest or same-group siblings; never assume it once grouping is in
    play.
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
   MkDocs `nav:` list, etc.). Add **one** TOC line linking to the new chapter, computed relative
   to the index file's own directory: `relative(dirname(index_file), chapter_file)`. The link's
   display text is the manifest entry's `title` verbatim — never the slug, never a paraphrase.
   Order alphabetically by display title unless the existing file uses a different order — match
   what is there. Do not rewrite unrelated rows. **For a grouped entry (`anyGroup` manifests),
   the line is wired under a `<group_title>` container** instead of directly into the flat list —
   see "Grouped index wiring" below.
   - **Degenerate — same-directory index** (`index_file: vault/handbook/SUMMARY.md`, chapter in
     the same `vault/handbook/` directory): `relative(dirname(index_file), chapter_file)`
     degenerates to the bare filename — `[Title](chapter-slug.md)`. This is the index→chapter
     direction (the TOC line points at the chapter); see "Chapter → index" above for the reverse.
   - **Repo-root index** (`index_file: SUMMARY.md`, chapter in `vault/handbook/`), same
     index→chapter direction: `relative(dirname(index_file), chapter_file)` climbs down from the
     repo root — `[Title](vault/handbook/chapter-slug.md)`.
2. **Glossary entry** — for each new domain term, add or link its entry under
   `{{publish.glossary_dir}}/index.md` (the page is owned by `references/glossary-discipline.md`;
   this adapter only encodes the relative link syntax).
3. **`{{publish.glossary_seed}}` reconciliation (conditional)** — only when `publish.glossary_seed`
   is set and readable, reconcile its row as that file's convention requires; when it is unset,
   proceed without it — a static docs tree often has no seed index.

**Step 0 — idempotency check, form-agnostic, and it runs BEFORE any container
classification.** This adapter only ever emits path links — `wikilinks: false` is a hard
requirement here (see "Halt conditions") — so the expected link target is always the same
coordinate system item 1 above uses:

`relative(dirname(index_file), chapter_file)` — step 0's own target.

Path-mode index matching is byte-identical on purpose (#311) — `locateChapterLine` is
called with NO `wikilink` option, so a target's terminal `.md` is never folded. Unlike
Obsidian, `handbook/orders` and `handbook/orders.md` are DIFFERENT hrefs on a static site
(one resolves, one 404s), so folding a divergent hand-authored row to a match would risk a
FALSE POSITIVE against a genuinely-different resource. A stale or divergent extensionless row
is therefore deliberately NOT matched: the "Flat entry, line absent" branch above appends the
canonical `.md` row, and the divergent row is RETAINED alongside it (append-and-retain). This
is a benign redundant index entry — both rows exist, the appended `.md` row resolves and
satisfies the link-integrity gate below (item 5 needs only ONE resolving index link), and the
machine round-trip stays exact — it is NOT a silent false-match. The link-integrity gate does
NOT remove or reject the retained divergent row: item 2 checks the CHAPTER's own relative
links, not an index-wide sweep, so catching a stale alias row would need an index-wide
broken-link/alias check (a possible future improvement, out of scope here).

Locate the chapter's current line by that target via `locateChapterLine(indexLines,
expectedTarget)` ⇒ `{present, containerTitle, indexForm, multiple}`. `indexForm` is
`'headings' | 'non-heading'`, computed from the file's own structural shape — NEVER inferred
from any single line's `containerTitle`. `containerTitle` is the nearest preceding heading; it
is `null` both when `indexForm` is `'non-heading'` (the file has no headings at all) AND when a
`'headings'`-form line sits above the first heading (an orphan line, correctly unplaced) — those
two `null` cases are not the same signal and are handled separately below:

- **Two or more lines match the target** ⇒ never guess which line is canonical; halt with:
  `Chapter '<slug>' appears multiple times in <index_file> — curate the index manually, then re-run.`
- **Flat entry, line present** ⇒ membership-only check passes; nothing else to do — no
  container to verify.
- **Flat entry, line absent** ⇒ not a step-0 halt — append the flat TOC line per item 1 above,
  exactly as shipped in 1.4.1, regardless of index form. Only a GROUPED entry's container
  machinery is headings-form-only.

**A grouped entry** (`anyGroup` manifests) — whether its line above came back present or
absent — is resolved in "Grouped index wiring" below, which reuses this same step-0 result
rather than locating the line a second time.

### Grouped index wiring (`anyGroup` manifests only)

Both shipped adapters wire the index before their link-integrity gate, so every wiring halt
below must be convergent on re-run: a first run halts with instructions, the container and
chapter line get added (by you, or by the user for a non-heading index), and the very next run's
step 0 finds them and proceeds without re-halting.

These outcomes reuse the step-0 result computed above (`containerTitle`, `indexForm`,
`multiple`) and cover a **grouped** entry only — step 0 above already decided the flat case:

- **Grouped entry, line present, `indexForm: 'non-heading'`** ⇒ wiring complete, proceed — a
  non-heading index has no container concept to verify against, so line presence alone is the
  whole check. The per-chapter line-presence check is deliberately form-agnostic; container
  verification below applies only when `indexForm: 'headings'`.
- **Grouped entry, line present, `indexForm: 'headings'`, and `containerTitleMatches(containerTitle,
  entry)`** (from `assets/lib/chapter-paths.mjs`; titles compare TRIMMED, not raw `===`) ⇒
  placement complete, move to the next chapter.
- **Grouped entry, line present, `indexForm: 'headings'`, `containerTitleMatches` false** ⇒ never
  silently move a user-curated line. This covers BOTH a line sitting under a different heading
  AND a line that sits outside every container (`containerTitle: null` — above the first `##`,
  or under an H1 with no `##` container yet): neither is correctly placed. Halt with:
  `Chapter '<slug>' is listed in <index_file> under '<found_title>' instead of '<group_title>' — move the line (or curate the index manually), then re-run.`
  When there is no enclosing container, fill `<found_title>` with a literal description such as
  `(none)` — the halt string itself never changes, only the substituted value does.
- **Grouped entry, line absent, headings-form index** ⇒ resolve the container (below).
- **Grouped entry, line absent, non-heading index form** (a nested list, an MkDocs YAML `nav:`,
  a bare path row) ⇒ halt with:
  `Index <index_file> is not a headings-form file — add a '<group_title>' container and the chapter line for '<slug>' manually, then re-run.`
  This is what makes the manual flow converge: you halt once with instructions, the user adds
  the container and the chapter line, and the re-run's step 0 finds the line present under the
  `indexForm: 'non-heading'` branch above and proceeds.

**Container resolution** — reached only for a grouped entry on a headings-form index once step 0
found no existing line. Locate the container by the entry's **current** `group_title`, which is
unique across groups (see `manifest-discipline.md`):

- **Zero candidates** ⇒ create a new `## <group_title>` heading matching the file's existing
  heading depth, then append the chapter line under it.
- **Exactly one candidate** ⇒ append the chapter line under it — append is always allowed, even
  under an inhomogeneous, user-curated container.
- **Multiple candidates** ⇒ halt with:
  `Found multiple '<group_title>' containers in <index_file> — curate the index manually, then re-run.`

**Automated grouped wiring works only on a Markdown-headings-form index.** For any other static
index form — a nested `SUMMARY.md` list, an MkDocs YAML `nav:` block — grouped index wiring is
**fully manual** in 1.5.0: you halt with the non-heading instructions above and stop there;
first-class non-heading container automation is a follow-up issue.

### Manual group migration

Wiring the index is establishment-only — it never renames, moves, or deletes an existing
container or chapter line. If a manifest review surfaces a `group` or `group_title` change on a
retained entry, or the removal of a grouped entry, that is **not** an index-wiring matter — it
is the manual-migration boundary. Halt with:

`This manifest change requires manual group migration (not automated in 1.5.0):`

followed by the per-entry change record, then:

`Follow the manual migration recipe in references/revalidation.md, then re-run.`

Do not attempt to wire, move, or delete anything yourself for the affected entries — follow the
recipe and the terminal-state checklist in `references/revalidation.md`, and re-run only once it
converges. An `anyGroup` flip (the manifest's first grouped entry appearing, or its last one
disappearing) is ALWAYS informational — see "Write-time canon" in `revalidation.md` — but that
note never suppresses a migration kind the same delta also carries: kinds always win. A flip with
ZERO change kinds (e.g. pure new-entry addition — never a migration matter) is note-only, exactly
as the note promises. Losing the manifest's LAST grouped entry, though, is a grouped-entry
REMOVAL in its own right — its own migration kind — so it still reaches this halt for cleanup;
the flip note rides alongside that halt, it does not replace it.

### Stale-artifact advisory (non-halt)

On every `anyGroup` manifest run, before you finish, list chapter files under
`publish.chapters_dir` and asset dirs under `capture.output_dir` that are **not** derivable from
the current manifest — i.e. no entry's `chapterRelPath` or `chapterAssetDir` matches them — and
print them as a warning pointing at the manual migration recipe in `references/revalidation.md`.
This is never a halt: a foreign, user-owned file is legitimate. But a manifest edited outside the
normal review flow can leave stale old-grouping artifacts behind with no delta to trigger the
boundary above, and this advisory is what surfaces them instead of letting them go unnoticed.

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
   files inside the published docs tree, so the retained screenshots must live within it. This
   check compares normalized resolved paths, so it holds unchanged for `chapterAssetDir(entry)`
   at any depth — a grouped entry's deeper `<group>/<slug>/` subdir is still inside `output_dir`
   and still covered. Halt with:
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
   under the entry's derived asset dir `chapterAssetDir(entry)` — `{{capture.output_dir}}/<chapter-slug>/`
   flat, `{{capture.output_dir}}/<group>/<slug>/` grouped, the retained location either way — AND
   that dir resolves under `{{publish.chapters_dir}}` so the static site can serve it — no orphan
   embeds, no captures the run did not produce, no embed pointing outside the published tree. This
   is a **resolution** check, not a spelling check: a retained chapter's older, still-resolving
   embed spelling stays valid; only an embed that fails to resolve into the derived dir fails here.
2. Every relative Markdown link resolves to a real file (and, for glossary links, a real heading
   anchor). Compute each from `relative(dirname(chapter_file), target_file)` and confirm the
   target exists. Broken relative links 404 on a static site and are silent in raw views.
3. The chapter has **at least one** link back to `{{publish.index_file}}` (navigability check).
   Unlike Obsidian, there is no graph view, so a missing second sibling link does **not** halt —
   one resolved index link is the minimum bar.
4. The frontmatter `language` (when frontmatter is required) matches `language.code`; the section
   labels match `publish.section_labels.prerequisites` and `publish.section_labels.related`
   verbatim.
5. `{{publish.index_file}}` lists the chapter with a link that resolves to it, computed as
   `relative(dirname(index_file), chapter_file)` from the index's own directory — the same
   coordinate system "Grouped index wiring" above uses for step 0.

A chapter that fails any of these is unpublished, not "almost done" — fix and re-verify.
