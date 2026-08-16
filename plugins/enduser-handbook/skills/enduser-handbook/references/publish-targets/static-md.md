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
independently pinned on `anyGroup`: the nested-list wiring `wireNestedListChapter` now emits
index lines and is directly unit-tested, but it does not itself consult `anyGroup` — the
adapter reaches it only inside the already group-gated grouped branch — so no direct
`anyGroup(...) === false` assertion exists on the index-line half; both mutation directions
there are exercised only transitively, through whatever wiring behavior consumes `anyGroup`.
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
  exactly as shipped in 1.4.1, regardless of index form. Only a GROUPED entry resolves a
  container, and that container machinery is form-restricted (a headings-form index, plus the
  bounded nested-list subset — see "Grouped index wiring" below).

**A grouped entry** (`anyGroup` manifests) — whether its line above came back present or
absent — is resolved in "Grouped index wiring" below, which reuses this same step-0 result
rather than locating the line a second time.

### Grouped index wiring (`anyGroup` manifests only)

Both shipped adapters wire the index before their link-integrity gate, so a wiring halt below is
convergent on re-run WHENEVER it names a form — and where one cannot be named it says so instead of
promising convergence (the unnamed 1.10.0 fallback below is the case, and it can repeat verbatim).
For the named ones: a first run halts with instructions, the container and
chapter line get added (by you, or by the user for a non-heading index), and the very next run's
step 0 finds them and proceeds without re-halting.

These outcomes reuse the step-0 result computed above (`containerTitle`, `indexForm`,
`multiple`) and cover a **grouped** entry only — step 0 above already decided the flat case:

- **Grouped entry, line present, `indexForm: 'non-heading'`** ⇒ call
  `verifyNonHeadingPlacement(indexLines, selectedTarget, group_title)` (`assets/lib/chapter-paths.mjs`,
  `selectedTarget` = step 0's own expected link target) and branch on the result:
  - **`ok`** ⇒ placement complete, move to the next chapter.
  - **`unverifiable`** ⇒ proceed — this file falls outside the verified class (see "Nested-list
    automation limits" below); the check ran and could not conclude — nothing further verifies
    placement, no confirmation is requested, and the run continues unverified, exactly as the
    shipped 1.10.0 behaviour did on this path. See the safety statement below for what this does
    and does not guarantee.
  - **`misplaced`** ⇒ halt reusing the exact headings-form wording above:
    `Chapter '<slug>' is listed in <index_file> under '<found_title>' instead of '<group_title>' — move the line (or curate the index manually), then re-run.`
    (`<found_title>` reads `(none)` when the line sits at the left margin, uncontained.)
  - **`inconsistent`** ⇒ a defensive contradiction check, not an outcome you should expect to
    reach: `verifyNonHeadingPlacement` re-runs `locateChapterLine` on the same `indexLines` and
    `selectedTarget` step 0 already scanned, and fires only if that re-scan now disagrees with
    step 0's own one-match count. Through this adapter's documented call path that cannot
    actually happen — `locateChapterLine` is a pure function of its inputs, so re-running it on
    the identical arguments reproduces the identical match count — so treat this as a fail-closed
    guard against a future caller shape, not a real file you will encounter. If it ever does fire,
    never guess which line is canonical; halt:
    `Chapter '<slug>' does not resolve to exactly one line in <index_file> — curate the index manually, then re-run.`
  This replaces the shipped 1.10.0 "line presence alone is the whole check" behaviour for the
  verified class only; every other non-heading file still proceeds unverified, unchanged.
- **Grouped entry, line present, `indexForm: 'headings'`, and `containerTitleMatches(containerTitle,
  entry)`** (from `assets/lib/chapter-paths.mjs`; titles compare TRIMMED, not raw `===`) ⇒
  placement complete, move to the next chapter.
- **Grouped entry, line present, `indexForm: 'headings'`, `containerTitleMatches` false** ⇒ never
  silently move a user-curated line. This covers BOTH a line sitting under a different heading
  AND a line that sits outside every container (`containerTitle: null` — above the first `##`,
  or under an H1 with no `##` container yet): neither is correctly placed. Halt with:
  `Chapter '<slug>' is listed in <index_file> under '<found_title>' instead of '<group_title>' — move the line (or curate the index manually), then re-run.`
  When there is no enclosing container, `<found_title>` is always the fixed literal `(none)` —
  the same literal the non-heading branch above substitutes for the same condition; the halt
  string itself never changes, only the substituted value does.
- **Grouped entry, line absent, headings-form index** ⇒ resolve the container (below).
- **Grouped entry, line absent, non-heading index form** (a nested list, an MkDocs YAML `nav:`,
  a bare path row) ⇒ attempt automated nested-list wiring: call
  `wireNestedListChapter(indexLines, group_title, <path-mode chapter link>)`
  (`assets/lib/chapter-paths.mjs`), where the chapter link is the same path-mode
  `[Title](<relative-index-path>)` form item 1 computes, and branch on its result:
  - **`{kind: 'inserted', newLines}`** ⇒ the index was a bounded nested-list container form;
    persist the returned `newLines` (joined back, they reproduce the exact bytes — EOL and
    terminal newline preserved) and proceed — the container was found or created and the
    chapter line inserted under it, no halt.
  - **`{kind: 'unwritable', field}`** ⇒ before returning, the writer re-reads the exact bytes
    it is about to hand back through its own reader — the same `prepareIndexLines` /
    `hasYamlMappingStructure` / `containerOwnerScan` pipeline a later run would use — and that
    reader would decline the result. Nothing is written. `field` names the manifest value the
    writer traces the refusal to: `'title'`, `'group_title'`, or `'unknown'` when neither
    stand-in clears it, found by substituting a known-good placeholder for one emitted line at
    a time and re-reading, so it stays correct for causes not yet catalogued. Render `<remedy>`
    from `field`. `'unknown'` is reachable only when no single emitted line's replacement clears
    the rejection, i.e. both values are independently at fault. `group_title` is GROUP-scoped —
    `validateGroups` requires every entry of a group to carry the same value — so a remedy that
    tells the operator to change it on THIS chapter alone does not converge: the next run halts
    on the conflicting-`group_title` gate instead. The three renderings:
    - `'title'` ⇒ `Give this chapter a plain title in the manifest.`
    - `'group_title'` ⇒ `Give a plain group_title to EVERY entry of this chapter's group in the manifest — it is group-scoped, so changing it on this chapter alone halts on the conflicting-group_title gate instead.`
    - `'unknown'` ⇒ `Give this chapter a plain title, and a plain group_title to EVERY entry of its group — group_title is group-scoped, so changing it on this chapter alone halts on the conflicting-group_title gate instead.`

    Halt with:
    `Cannot wire '<slug>' into <index_file>: the lines this run would write are not recognizable to the next run, so nothing was written. <remedy> Then re-run. For this recovery step, use a non-empty value made only of Unicode letters and numbers, with words separated by single ASCII spaces. That positive constraint is deliberately narrower than the parser's full accepted language; it was verified across both link modes and all three bullet markers of the line being written, regardless of markers elsewhere in the file. See "Nested-list automation limits" below for the measured per-marker set.`
  - **`{kind: 'present', index}`** ⇒ the single matched container already carries a child
    bullet whose content is byte-identical to the chapter link the adapter is about to write —
    the writer's own membership guard, checked directly against the list body and independent
    of step 0's target parse (see "After either halt" below for why the two can disagree and
    what this bounds). Halt with:
    `Chapter row for '<slug>' is already present under the '<group_title>' container bullet in <index_file>, but this run could not recognize it — the chapter's own title does not yield a resolvable link destination. For this recovery step, give the chapter a non-empty title in the manifest made only of Unicode letters and numbers, with words separated by single ASCII spaces. That constraint was verified across both link modes and all three bullet markers of the line being written. Then re-run; see "Nested-list automation limits" below.`
  - **`{kind: 'multiple'}`** ⇒ two or more container bullets match `group_title`; never guess
    which is canonical, halt with:
    `Found multiple '<group_title>' container bullets in <index_file> — curate the index manually, then re-run.`
  - **`{kind: 'not-a-list'}`** ⇒ the index is not in the automatable nested-list subset (see
    "Nested-list automation limits" below) — a YAML `nav:`, a bare path table, or a list shape
    outside the bounded safe subset. **Verify the named form before naming it — a halt is
    convergent only if the exact pair it prescribes would actually be recognized on the very
    next run, so check that, rather than promise it:**
    1. build the candidate as its own two-line array: `- <group_title>`, then on the next line
       `  - [` + title (any `]` escaped as `\]`) + `](<` + path + `>)` indented two spaces under
       it — the same escaping the named halt below promises, so the gate validates the exact
       spelling the operator is told to type;
    2. run `locateChapterLine(<candidate>, <index-relative-path>)` (`assets/lib/chapter-paths.mjs`,
       no `wikilink` option) on that two-line array alone, not the real index;
    3. run the fixed-probe writer predicate on the same array —
       `wireNestedListChapter(<candidate>, group_title, <fixed probe link>)`;
    4. **the candidate's own text, split on newlines, is exactly two physical lines — not merely
       two array elements, since an embedded newline inside `title` or `path` can add more;
       exactly one match; that match's `matches[0].index === 1` (`LocateChapterLineMatch.index`,
       `assets/lib/chapter-paths.mjs`) — the indented chapter line, never the container line at
       index 0; and the predicate returning `{kind: 'inserted'}`** ⇒ the pair is representable —
       emit the convergent halt naming it exactly:
       `Index <index_file> is not a headings-form file — add a '<group_title>' container and the chapter line for '<slug>' manually, then re-run. The next run recognizes the chapter line as a Markdown list row INDENTED TWO SPACES under the '<group_title>' container bullet, whose link destination is exactly '<index_relative_path>' — that is, a '- ' + group_title line followed by a '  - [' + title + '](<' + path + '>)' line, with the destination inside angle brackets and any ']' in the title escaped as '\]'. Give the row a plain title — no Markdown markup, backslash escapes, or HTML entities in it — or the next run may not be able to confirm its placement; see "Nested-list automation limits" below for exactly what is recognized.`
    5. **anything else** ⇒ the gate rejects the pair — measured causes include an ordinary
       newline inside the title, a trailing `\` or a `>` in the target, and a `group_title` the
       writer's own bullet grammar refuses (padded with extra whitespace, or carrying markup).
       Emit the plain, unchanged 1.10.0 halt instead, with no named form and no convergence
       claim:
       `Index <index_file> is not a headings-form file — add a '<group_title>' container and the chapter line for '<slug>' manually, then re-run.`
       The operator is no worse off than before 1.11.0 here — this halt can repeat verbatim on
       the next run, exactly as it always has. The gate never names a pair that would not
       converge, but it also never claims convergence it has not checked. See below for what
       either halt does and does not prove once it fires.

    **After either halt — what the gate does and does not prove.** Item 4's convergence promise
    holds — but only for a `group_title`, target and title the gate accepts: you halt once with
    instructions, the user adds the container and the chapter line, and the re-run's step 0 finds
    the line present under the `indexForm: 'non-heading'` branch above and proceeds. One
    operator-actionable warning belongs here too, and it is narrower than "markup in the title":
    it applies to a title whose markup keeps the row's own link target from resolving — a nested
    link, a nested image, a reference link, or an unescaped `]` in the title (the halt above tells
    the operator to escape it as `\]`; skipping that produces exactly this same failure).
    Convergence depends on the manifest entry's own `title` — not on whatever row already sits
    in the index — because that is what the writer rebuilds its inserted row from on every run.
    If an existing row (operator-typed, or left over from any prior run) does not resolve but the
    manifest title is clean, the writer's own insert resolves immediately: the earlier,
    unrecognizable row lingers beside it as a cosmetic duplicate, and the very next run reports
    `ok` on the clean one. That is the one combination that converges with a harmless leftover;
    the other combination — the manifest title ITSELF target-breaking — used to diverge by
    marker, before 1.11.0's re-read postcondition on `wireNestedListChapter` (see the
    `unwritable` outcome under "Grouped index wiring" above):
    The controlling marker is the marker of the child row the writer is about to emit — not a
    file-wide style, and not necessarily the container's: under an existing container the
    writer reuses the last existing child's marker, falling back to the container marker only
    when there is no child. On creation both new lines instead copy the first indent-0 bullet's
    marker, so there the emitted marker IS read off the file — off that one bullet, not off any
    file-wide style. Therefore:
    - **Nested under its single matched container, when the new child row uses `-`** is unaffected by the
      postcondition for THIS class of title — a `]` that breaks the row's own link parse but
      leaves the line an ordinary bullet. A `-`-marked child row is not exempt in general:
      measured, a backtick, an HTML comment or a U+2028 in the same position is refused on a
      `-` child exactly as it is on `*`/`+`, because those change how the line itself
      parses rather than only how its target does. For the `]` class, though: every later
      run still finds `containers.length === 1`, and step 0 still reports the chapter absent —
      the row is exactly as unrecognizable to step 0's target parse as before — but the
      writer's own membership guard (the `present` outcome above) now recognizes its own prior
      insert VERBATIM, refuses to write a second copy, and halts instead. Exactly ONE row is
      ever written here; the shipped 1.10.0 behaviour this retires had no membership check at
      all and appended another duplicate row on every re-run, without limit.
    - **The same nested placement, but when the new child row uses `*` or `+`** used to lock the whole
      file out on the NEXT run instead: `chapterRelPath` (`chapter-paths.mjs:168-172`) always
      joins the entry's `group` onto its `slug` — `<group>/<slug>.md` — whenever `group` is
      set, so a title that breaks its own link parse leaves that group-prefixed `/` sitting in
      the raw bullet content, and `isBarePathBullet` refuses exactly that shape on a `*`/`+`
      child marker. **As of 1.11.0, the writer catches this on the SAME run instead**, because the
      re-read postcondition runs that identical check over its own output before returning: the
      write is refused — `{kind: 'unwritable', field: 'title'}` — and nothing is persisted, so
      there is no poisoned file left for a later run to trip over.

    A `group_title` that is itself non-plain is refused immediately, before the writer ever
    looks at containers or existing rows — `wireNestedListChapter` checks the group axis first,
    so a malformed `group_title` returns `not-a-list` right away, regardless of the chapter
    title or the row's placement, on every run, with nothing ever written. This is unchanged
    since 1.10.0: it was never a silent-write case to begin with. A hand-typed row already
    sitting at the left margin (indent 0), uncontained — its own label failing that same
    indent-0 plain-label check (see "The plain-label predicate, named exactly" below) — locks
    the whole file out the same way, for the same reason: the writer's refusal to add anything
    to such a file is the correct response to it, not something 1.11.0 either causes or fixes.

    Measured, across every placement × title-resolvability combination that matters here, with
    the manifest's chapter `title` held FIXED across every run: a row that already resolves
    inserts nothing further; a stale row alongside a clean current manifest title gives one
    lingering duplicate then `ok`; a target-breaking current title nested under its container, on
    a `-` child, converges on exactly one row, then a `present` halt from the second run
    onward (new in 1.11.0 — this is the case #330 retires from unbounded); the same title nested
    under its container with a `*` or `+` child now returns `unwritable` on the very first
    attempt, before any row exists (also new in 1.11.0); a non-plain `group_title` on any marker
    is refused immediately, before any row exists, unchanged since 1.10.0. No combination
    measured here, with the title held fixed, grows without bound.
    **That fixed-title scope is load-bearing, not incidental: letting the title itself change
    across runs reopens unbounded growth whenever the emitted child is `-`, nested under its
    container.** Step 0's presence check and the writer's own membership guard both key on the CURRENT
    manifest title's own link string, never on whatever row already sits in the index — so an
    operator who edits a target-breaking title (say, re-wording an unescaped `]` differently)
    between publishes hands each edit its OWN distinct link string, one the membership guard has
    never seen before and therefore inserts as a new row every time. Measured (`-` child, 20
    publishes, the title edited once every four runs): 5 rows accumulate, one per edit, none
    removed. The run is not silent — the other 15 publishes each return `present` and the adapter
    halts on it — but no halt ever names the orphaned rows, so growth here is bounded only by the
    number of distinct titles the operator has typed, which in practice is unbounded. Within the
    harmless-manifest-title
    case above, a title whose markup still decodes to a plain label is verified like any
    other plain title — `[A\.B](x.md)` decodes to the plain `A.B` and is `misplaced` at the left
    margin, `ok` correctly nested. A title that stays non-plain even after decoding — an
    ampersand, emphasis, an HTML entity — is harmless too, just not fully verified: at the left
    margin its own label fails the plain-label check, so the whole scan declines and the adapter
    proceeds on `unverifiable` (see "Nested-list automation limits" below for the measured
    table) — but nested under its container it is `ok` regardless, since `isPlainLabel` is never
    applied to a child bullet, only to an indent-0 one. That list is representative, not
    exhaustive, and deliberately excludes a run of backticks or an HTML comment: `isPlainLabel`
    skipping the child bullet does not mean nothing else reads it there. A backtick run — any
    length, a single backtick and a triple run are the same mechanism — opens an unterminated
    inline code span, never a fence: a fence needs the run at the very start of a physical line,
    which an emitted chapter row (`<indent><marker> [<title>](<target>)`) can never be. Both it
    and an HTML comment open a real inert construct in the persisted file, so a separate,
    file-wide check catches them regardless of indent — never harmless nested, unlike the
    markup above (see "Nested-list automation limits" below — the writer itself now refuses to
    write either; a hand-typed one in the index still costs the whole file the same way). Use a
    plain-text title — free of backticks and HTML comments above all — to avoid needing any of
    these distinctions.

    The gate above is checked on the candidate's own isolated two-line array. **By construction,
    that proves only that the candidate pair is well-formed and would be recognized on its own —
    it proves nothing about the real index, because it never reads the real index.** Any property
    of the real file that makes the shipped locator or writer decline can still diverge from what
    the isolated check found; the cases below are measured illustrations, not a closed list:
    - an inert region (a fenced code block, an HTML comment) blanks a representable pair, so it
      is reported absent again — repeating the convergent halt above, never completing;
    - a chapter row that exists only inside leading frontmatter is **not** a wired row: since #337
      the locator blanks a closed leading frontmatter block exactly as the writer's own body
      preparation always did, so both read the same file and the pair is reported absent. The run
      then wires a real row in the body — convergence, not an unverified completion. (Before #337
      the locator alone reported it present, and the present-line branch answered `unverifiable`;
      that shipped 1.10.0 view disagreement is closed.) An UNCLOSED leading `---` is still not
      frontmatter — it is a YAML document-start marker and exempts nothing;
    - a real index whose surroundings carry YAML structure, a wildcard, or an ordered list makes
      the writer decline the whole file on the next run too: once the pair is present, step 0
      routes to the present-line branch above, whose own predicate call declines the same way,
      and the adapter again proceeds on `unverifiable` rather than a repeated halt.

    A future case diverging some other way is expected, not a defect in this documentation — the
    isolated check was never designed to rule any of this out.

    **The honest safety statement,
    scoped to what this PR governs: on the non-heading branch above, this gate
    never lets a MISPLACED row complete silently when it can verify placement.** Wherever it
    cannot conclude — the check never runs, because the line was never even reported present, or
    it runs and returns `unverifiable` — the run falls back to the same unverified completion
    named just above: it is not that a false completion cannot occur there, and not that every way
    it can occur is named above.

    **The headings branch no longer completes silently on that shape (#337):** a chapter row inside
    a valid frontmatter block whose body itself carries a heading used to be reported
    `indexForm: 'headings'` with a matching container (see "Grouped entry, line present,
    `indexForm: 'headings'`" above) and completed with neither verification nor confirmation. The
    locator now blanks the frontmatter span, so such a row is absent on both branches and the run
    wires a real one in the body.

**Container resolution** — reached only for a grouped entry on a headings-form index once step 0
found no existing line. Locate the container by the entry's **current** `group_title`, which is
unique across groups (see `manifest-discipline.md`):

- **Zero candidates** ⇒ **not automatically a create.** Zero is two facts wearing one name: this
  group has no container yet, or one exists and the equality compare failed for a reason the match
  cannot see. Before creating anything, re-read the container headings you already hold and ask
  whether any of them plausibly RENDERS as `group_title` — comparing both sides under NFC (the
  normalization the match itself already applies), case folding, unwrapping a whole-content
  emphasis, markdown-link or wikilink wrapper, stripping a trailing `{#anchor}`, and dropping a
  leading run of non-letter decoration such as an emoji or an icon. That comparison DETECTS a near
  miss and nothing more: it never selects a container to write into, so it cannot mis-target, and
  the write still needs the exact match that already came back empty.
  - **One or more headings are a plausible spelling** ⇒ halt naming both spellings, with every
    invisible codepoint in the heading escaped, so the operator can see a difference the terminal
    will not render:
    `Found no container titled '<group_title>' in <index_file>, but the heading '<heading>' may be the same section — rename the heading to match group_title, or change group_title, then re-run.`
    Convergent like every other named halt here: the operator makes the two spellings agree, and
    the next run's exact match resolves to one container.
  - **None is** ⇒ the create is safe: create a new `## <group_title>` heading matching the file's
    existing heading depth, then append the chapter line under it.
  This deliberately over-rejects, in the same direction as the plain-label refusal this adapter
  already applies on the nested-list path: a `group_title` of `Reports` halts against an existing
  `reports` heading that belongs to a different group. A halt costs one edit; a forked index is
  silent, permanent and never self-corrects, because the next run matches the heading it wrote
  itself.
- **Exactly one candidate** ⇒ append the chapter line under it — append is always allowed, even
  under an inhomogeneous, user-curated container.
- **Multiple candidates** ⇒ halt with:
  `Found multiple '<group_title>' containers in <index_file> — curate the index manually, then re-run.`

**Automated grouped wiring covers a Markdown-headings-form index and a bounded nested-list
(GitBook `SUMMARY.md`) container subset.** A headings-form index resolves its container as
above; a non-heading index whose shape falls inside that bounded subset (see "Nested-list
automation limits" below) is wired by `wireNestedListChapter` per the line-absent branch above.
Every other static index form — an MkDocs YAML `nav:` block, a bare path table, or any list
shape outside the safe subset — stays **fully manual**: you halt with the non-heading
instructions above and stop there. First-class YAML `nav:` container automation remains its own
follow-up, #328. Path-table container automation, by contrast, is not merely deferred: it was
decided against as not soundly automatable — see #340 for the recorded reasoning.

### Nested-list automation limits

`wireNestedListChapter` automates only a **bounded, conservative** nested-list subset and
defers everything else to the manual `not-a-list` halt above — safety over reach.
The two recovery classes referenced from the halts above differ deliberately: the #329 manual
rows reach the next run's proceeding branch, with
U+2028/U+2029 separators proceeding as `unverifiable`. A `present` recovery must also survive
the writer's re-read of the bytes it would emit, so its title constraint is narrower.

It wires an index only when it is a plain bullet list whose container labels **and** the entry's
`group_title` are plain-text: it refuses any label or `group_title` carrying inline markup or
a leading block trigger — emphasis, a link inside the visible text, an image, raw HTML, an
entity, a **bare** backslash escape, inline code, a leading `#` heading or list marker, or a run
of collapsing whitespace — because a character allowlist cannot prove such a label renders equal
to a plain `group_title`, so matching it could miss a real container or manufacture a
duplicate. The escape refusal applies to the label's raw, literal spelling only: a whole-content
markdown link or wikilink wrapper is unwrapped before the plain-label check ever runs, but only
the markdown-link half also decodes its escapes — so `Admin\.X` written bare is refused,
`[Admin\.X](x.md)` decodes to the plain `Admin.X` and is accepted, while the identical escape
written as a wikilink alias keeps its literal backslash and is refused just like the bare form:
matching a markdown-link label decodes its backslash escape, not what it renders as in full (an
HTML entity is never decoded either way — see "The plain-label predicate, named exactly" below);
matching a wikilink alias is against its literal, undecoded text. (This adapter never emits
wikilinks and this file deliberately contains no wikilink syntax — see `obsidian-vault.md` for
the spelled-out form.) It also refuses a `*`- or `+`-marked bullet whose visible text is a
**bare (non-link) path** — one containing a `/` or backslash separator, or ending in `.md` —
because
the shipped membership scan only sees `-`-marked bare rows, so wiring such a file could create
a second container beside a retained phantom row (a legitimate `*`/`+` plain label that happens
to contain `/` is refused too, a deliberate over-rejection, not corruption). Inline code, an
HTML comment or a fenced block anywhere, a mixed or bare-CR line ending, a YAML `nav:` or
`- key: value` mapping bullet, a list nested more than one level deep, and a multiline
`group_title` fall outside the subset as well. No plain-label check gates the chapter title
itself (only the container label and `group_title` above are checked — see "The plain-label
predicate, named exactly" below), so nothing here stops a legal manifest title from carrying a
run of backticks — any length, a single backtick and a triple run are the same mechanism: an
unterminated inline code span, never a fence, since an emitted chapter row can never sit at the
start of a physical line the way a real fence requires — an HTML comment, or a U+2028/U+2029
line separator. A `group_title` colon straight after its first token, or a `group_title` of two
or more hyphens, is a different class again: both pass the plain-label check above (colons and
interior hyphens are allowed there) and are only caught downstream, when `hasYamlMappingStructure`
or `NESTED_THEMATIC_BREAK_RE` reads the emitted container line back. **Both of those downstream
gates are `-`-only** — `hasYamlMappingStructure` strips a leading `-` before testing, and
`NESTED_THEMATIC_BREAK_RE` matches a run of the same character — so this class is caught only
when the newly-created container line uses `-`. Measured: when that line uses `*` or `+`,
`FAQ: basics`, `Admin:`, `---` and `--` all return `inserted` and the container line is written
as given. The `*`/`+` fatal `group_title` class is the disjoint one directly above: a `/`
anywhere, or a trailing `.md`. On container creation the writer uses the first indent-0
bullet's marker, not a file-wide majority or the markers used later in the file.

**As of 1.11.0, none of these write a poisoned file.** `wireNestedListChapter` re-reads the
exact bytes it is about to persist through this same reader before returning (see the
`{kind: 'unwritable', field}` outcome under "Grouped index wiring" above), and any value that
would leave the file unrecognizable to a later run fails that re-read. The write is refused
outright — nothing is persisted — instead of succeeding once and leaving every later run, for
every chapter and group in the file, to fall back to unverified completion or a repeating,
unnamed halt. This refusal is conservative, not rendering-aware: it can decline a title or
`group_title` that would in fact have rendered and parsed fine, because it checks structure, not
rendered output. The remedy is a manifest value, not an index edit — and it is the recovery class
the halt itself states, not the parser's broader plain-label class: Unicode letters and numbers,
single ASCII spaces between words, applied to the chapter's title or to EVERY entry of its group.
Do not read "plain" here as the predicate named below. That predicate accepts `FAQ: basics`,
`Sales/Marketing` and `billing.md`, each of which is refused by some emitted marker, so an operator
who satisfies it and re-runs can meet the identical halt again. A richer rendering-aware matcher
remains a possible
follow-up for the container-label and `group_title` refusals earlier in this section, not for
this gap.

**The plain-label predicate, named exactly.** In short: a plain title is verified; a non-plain
title that still resolves is found but left unverifiable; a title that breaks its own row's
link target is caught by the writer's membership guard when the emitted child uses `-`, or by
the re-read refusal when it uses `*`/`+`, rather than duplicated without limit for a fixed title
(see "After either halt" above for the marker rule and the title-edit caveat). The
mechanism: the container-owner scan (`containerOwnerScan`,
`assets/lib/chapter-paths.mjs`) applies `isPlainLabel` to whatever `extractLabel` returns for a
row's own content — never to the row's raw source text, and never to what it renders as in a
browser — and it applies this check to EVERY indent-0 bullet in the file, not only the row under
test: a single non-plain indent-0 label anywhere in the file declines the WHOLE scan
(`{kind: 'not-a-list'}`), so an otherwise-clean
'Admin' container elsewhere in the file cannot rescue a badly-labelled row sitting at the left
margin. `extractLabel`'s own decoding differs by the label's link syntax: a whole-content
markdown link decodes backslash escapes before the check runs (`[A\.B](x.md)` becomes the plain
`A.B`), a whole-content wikilink alias does **not** decode them (the alias keeps its backslash
and stays non-plain), and an HTML entity is never decoded by either form — so a title
that LOOKS plain once rendered in a browser can still fall outside the verified class. Measured
for a row sitting AT THE LEFT MARGIN alongside a clean, correctly-formed 'Admin' container
elsewhere in the same file:

| Row source                        | `extractLabel`             | `isPlainLabel` | Verdict          |
|------------------------------------|-----------------------------|----------------|------------------|
| `- [A.B](<items.md>)`              | `A.B`                       | true           | `misplaced`      |
| `- [A\.B](<items.md>)`             | `A.B`                       | true           | `misplaced`      |
| `- [A&#46;B](<items.md>)`          | `A&#46;B`                   | false          | `unverifiable`   |
| `- [A & B](<items.md>)`            | `A & B`                     | false          | `unverifiable`   |
| `- [A *b*](<items.md>)`            | `A *b*`                     | false          | `unverifiable`   |
| `- [See [here][ref]](<items.md>)`  | *(target never resolves)*   | —              | absent at step 0 |

The last row is a different failure mode entirely: a nested link inside the label breaks the
row's OWN link-target extraction — not `extractLabel`/`isPlainLabel` at all — so step 0 never
reports the chapter present in the first place; see "Grouped index wiring" above for what a
target-breaking title does instead: one insert and then a `present` halt when the child marker
is `-`, or an `unwritable` refusal before insertion when it is `*`/`+` — never unbounded
per-re-run duplication for a fixed title.

**Two labels that LOOK identical (#351).** Container labels are compared after Unicode NFC
normalization, so a `group_title` typed with a combining accent matches a container written with
the precomposed letter — the same label, two encodings, one container. A container the NESTED-LIST
writer creates is written in NFC, because it emits the normalized key it matched on; the
headings-form create step is instructed prose, and writes the `## ` heading with the `group_title`
exactly as the manifest spells it — which still resolves later WHATEVER ITS NORMALIZATION, in either
direction, because it is the COMPARISON that is normalized rather than the file. (That promise
covers normalization only. A `group_title` carrying a line break — a raw newline, a CR, U+2028 or
U+2029 — passes both the manifest gates and the plain-label check and still never resolves; the
newline case re-creates its container on every publish. Pre-existing, unrelated to normalization,
and filed separately.) An existing line is never rewritten either, so a
file may legitimately end up holding an older decomposed container beside a newly created
precomposed one for a DIFFERENT group. Two consequences you must know before running this on an
index published by 1.10.0–1.12.0:

- **an index that already accumulated BOTH spellings of one label now halts** with
  `Found multiple '<group_title>' container bullets in <index_file> — curate the index manually,
  then re-run.` They are one container now, so a human has to merge them: move the child rows
  under whichever line you keep and delete the other. The two lines are pixel-identical — find
  them with `python3 -c "import sys,unicodedata; [print(i+1, repr(l)) for i,l in
  enumerate(open(sys.argv[1])) if l != unicodedata.normalize('NFC', l)]" INDEX.md`, which prints
  exactly the lines that are not already NFC;
- **a label carrying an invisible character is refused, not normalized** — a zero-width space, a
  soft hyphen, a bidi control or a byte-order mark. No normalization can merge two such labels and
  picking one would be a guess, so refusal is the honest outcome; the repair is to delete the
  character. It is enforced in two places, and the scope of each is exact:
  - in the MANIFEST, by `validateGroups`, which halts naming the entry and the group — so a
    `group_title` carrying one never reaches this step at all;
  - in a NESTED-LIST index, by `isPlainLabel` inside the container scan, which declines the WHOLE
    file (`{kind: 'not-a-list'}`) exactly as any other non-plain indent-0 label does, including for
    unrelated groups in the same file. The generic halt does not name the cause, which is why it is
    named here.

Three residuals stay open, deliberately. **A HEADINGS-form index is not covered by the second
rule**, and the gap it leaves is a CLASS rather than one exotic character: a `## ` container
heading is never refused, so any heading that renders as `group_title` without being byte-equal to
it fails the match. Measured against the shipped comparison — bold, a markdown link, an emoji
prefix, a trailing `{#anchor}` and a case difference all fail it, and an emoji prefix is ordinary
index curation rather than an edge case. The near-miss check in the container-resolution branch
above now HALTS on that whole set instead of creating beside it, so what remains here is only what
that check deliberately cannot recognize: a heading whose label carries an invisible character is
neither refused nor read as a near miss, and this adapter still creates a second, pixel-identical
heading beside it. That remainder is unchanged pre-existing behaviour, and it stays open by choice
rather than for want of a mechanism — the near-miss comparison could ignore invisible characters
too, and the sentence immediately below is why it must not. U+200C/U+200D (ZWNJ/ZWJ)
are still accepted everywhere, because they are required INSIDE ordinary words in Persian, Hindi
and other scripts and refusing them would lock out a correctly-spelled title — so two labels
differing only by one are still two containers. So are two labels differing by a no-break space
versus an ordinary one (U+00A0 renders as a visible space, a different class).

As of 1.11.0, a **present** grouped chapter's placement under this container is also checked,
but only for a narrow verified class — this exact sentence, reused verbatim wherever it is cited,
today `revalidation.md`'s "Terminal-state convergence checklist" and `obsidian-vault.md`'s own copy.
The 1.11.0 CHANGELOG entry is NOT one of those copies: it keeps the narrower two-outcome sentence
that was true when it shipped, because it is a record of that release rather than a statement of
current behaviour, and the suite pins it separately for exactly that reason:

files for which the fixed-probe writer call returns `kind === 'inserted'`, `kind === 'present'`,
or `kind === 'unwritable'` with `field === 'group_title'`, and which hold exactly one
selected-target match (a row inside a closed leading frontmatter block is not a match at all).

**In practice:** this is the subset above, minus a selected target that resolves to zero lines
or to more than one (`inconsistent` — see "Grouped index wiring" above). The third accepted
outcome is #350's: a `group_title` the writer cannot EMIT — YAML-mapping-shaped, or
bare-path-shaped under a `*`/`+` marker — says nothing about where the row already on disk sits,
so its placement is still compared. That member can only ever conclude `misplaced`, never `ok`:
the writer reaches the branch that would emit a container precisely when no existing container
matches. Operators land on `unverifiable` rather than inside the verified class most often for
one of: a Markdown nav file using a wildcard, an ordered list, or an explicit `<!--nav-->`
marker (all ordinary `mkdocs-literate-nav` features); two same-named containers; a `group_title`
that is not a plain label (a construct-bearing or invisible-character-bearing one — its rendered
form does not equal its literal form, so no comparison is sound); or a **native/YAML MkDocs
`nav:` configuration**, which gets no placement verification at all (see the safety statement
above under "Grouped index wiring") — the run completes unverified, exactly as before 1.11.0,
with no confirmation requested. First-class YAML `nav:` container automation remains its own
follow-up, #328.

Three disclosures the operator is owed, not proved away:

- A `SUMMARY.md` holding more than one Markdown list — `mkdocs-literate-nav` honors only the
  *last* one, while this machinery scans indent-0 bullets across the whole file, so a row can
  verify against a list the tool ignores. The shipped writer already carries this exposure;
  1.11.0 does not widen it.
- A bullet-only file that also happens to be valid YAML — an `ok` now verifies placement where
  1.10.0 completed silently with no check at all: a Markdown-reading answer about bytes some
  other consumer may read as YAML.
- A chapter row sitting inside leading frontmatter is not verified because it is not a row: both
  branches now read it as absent (#337, below), so the run wires a real one in the body. What is
  still owed: nothing here proves the YAML it was embedded in meant nothing to another consumer.

**The frontmatter view disagreement is FIXED (#337).** The writer's own body-preparation view
always blanked a closed leading frontmatter block before wiring; the step-0 locator's view did
not, so the two sides disagreed about what a frontmatter-embedded chapter line meant — and,
worse, one backtick inside a YAML scalar opened an inline-code span that blanked the REST of the
document for the locator alone. A headings-form index then read as absent AND non-heading, so the
run routed it into the nested-list writer, which appended a bullet-shaped container plus a
duplicate row on every publish (the shipped 1.10.0 frontmatter bug). Both views now apply the one
shared rule, so a closed frontmatter block is invisible to both, and neither the false "already
wired" report nor the per-publish duplication can arise from it. Scope: a CLOSED block only — an
unclosed leading `---` is a YAML document-start marker, exempts nothing, and still lets a stray
backtick inside it swallow the body (the writer refuses such a file outright).

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
