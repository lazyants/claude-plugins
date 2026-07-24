# Publish target: Obsidian vault

You read this file when `publish.target: obsidian_vault`. It is the Obsidian-specific
publish-target adapter (the `static_md` adapter publishes to a plain-Markdown docs tree
instead). Every path here resolves through profile keys — never hardcode a project layout. Obsidian-specific names (Dataview, wikilinks, INDEX.md, `.md` frontmatter)
are deliberate: that is this adapter's job.

## What "Obsidian vault" implies

The publish destination is a folder tree of plain Markdown files inside an Obsidian vault.
You can rely on three Obsidian-specific features:

- **Wikilinks** — `[[path/to/note|display text]]`. Enabled when `publish.wikilinks: true`.
  When `false`, fall back to standard Markdown links for every link this adapter writes —
  internal chapter links, glossary links, and the Related block below all still apply,
  just in the standard-Markdown form ("Wikilinks vs Markdown links" below) instead of
  wikilink syntax.
- **Dataview** — code-fenced ` ```dataview ` queries that render as live tables/lists.
  Only emit Dataview if the vault already uses it; do not introduce it unprompted.
- **INDEX.md convention** — each top-level vault section has an `INDEX.md` that tracks
  status rows for its sub-sections.

You do not own the vault. The user may already have a custom layout, Dataview dashboards,
templater scripts, and graph-view conventions. Add to it; never restructure it.

## Vault root

Every path this adapter resolves against the vault itself (as opposed to the project
root — see "Coordinate systems" below) is expressed relative to `<vault-root>`, a
directory this adapter derives once per run. An optional `publish.vault_root` profile
key may name the vault root directly; when set it takes precedence over the `.obsidian/`
discovery below (see "Override" in the Selection block).

**Selection — one anchor, no tie-break.**
**Override — `publish.vault_root` short-circuits discovery.** When `publish.vault_root`
is set it names `<vault-root>` directly: canonicalize it through the "Path
canonicalization" rules below, but require the fully-resolved path to be an existing
readable **directory** — the ENOENT trailing-suffix allowance there does NOT apply to
`publish.vault_root`, so the vault root must exist. That directory IS `<vault-root>`;
the `.obsidian/` walk and BOTH the zero-marker and two-or-more-marker halts below are
bypassed entirely. On a non-directory, missing, or unreadable override, halt:
"publish.vault_root '<value>' does not resolve to an existing readable directory —
create the vault directory, or correct publish.vault_root to name an existing Obsidian
vault (it must be a directory, not a file or a not-yet-created path)."
With no `publish.vault_root` set, the only discovery anchor is `publish.chapters_dir`
— a `chapters_dir` that already IS the vault root counts too. Walking upward through
its ancestors, collect every ancestor that holds a readable `.obsidian/` directory, all
the way to the filesystem root — stop once an ancestor's own parent is itself, never
earlier. There is deliberately no lower bound on the walk: stopping at the project root
would wrongly exclude an absolute `chapters_dir` that legitimately points outside the
project (every `publish.*` value may be absolute — see "Coordinate systems" below).

- **Exactly one** `.obsidian/` ancestor ⇒ that directory is `<vault-root>`.
- **Zero** found ⇒ halt: "No Obsidian vault found above `<chapters_dir>` — open the
  vault in Obsidian once so `.obsidian/` exists, or point `publish.chapters_dir` inside
  an existing vault, then re-run."
- **Two or more** found ⇒ halt: "Multiple `.obsidian/` ancestors found above
  `<chapters_dir>` — set `publish.vault_root` to name the active vault. Neither the
  innermost nor the outermost marker is a safe default: a stale nested vault defeats
  innermost, a genuine nested vault defeats outermost, and disk markers alone cannot
  tell the two apart — only the operator knows which vault is active." This halt is
  deliberately fail-closed: a confident wrong guess would silently publish into the
  wrong vault, and resolving it today means removing or relocating a stale `.obsidian/`
  yourself, or setting `publish.vault_root` to name the active vault — this skill will
  not do that automatically.
- **An ancestor exists on disk but is unreadable during the walk** ⇒ halt, naming the
  exact path: "Cannot read `<path>` while walking for the vault root — grant
  read/execute access (e.g. `chmod +rx <path>`), or re-run as an account that can
  traverse it." Never treat an unreadable ancestor as "no marker here" — silently
  skipping past it would let the walk continue and select an outer, wrong vault.

**Validation — everything else is tested against `<vault-root>`; nothing else selects
it.** `publish.chapters_dir`, `publish.index_file` and `publish.glossary_dir` must each
resolve **under** `<vault-root>` — a failure here means that path is wrong, never that a
wider root should be chosen instead. `publish.glossary_seed` participates in neither
selection nor this validation: the schema requires the key, but the base skill only
consumes it "when set and readable", so an empty value is a legal "unset" and this
adapter does not strengthen that to mandatory (see "INDEX wiring" below).

**Path canonicalization — defined for paths that may not exist yet.** A first-run
`chapters_dir`, or an `index_file` whose file has not been created yet, cannot be
resolved with a plain `realpath`. Resolve in this order: (1) turn a project-root-relative
value into an absolute one — an already-absolute value passes through unchanged; (2)
lexically normalize `.` and `..` segments; (3) canonicalize the longest **existing**
ancestor, resolving its symlinks; (4) re-append the normalized non-existent suffix; (5)
compare paths with a **segment-aware** prefix/equality test, never a raw string prefix —
`/vault2` must never count as inside `/vault`; (6) an `ENOENT` **in the non-existent
trailing suffix** is expected on a first run and is not an error, but every other
resolution failure (`ENOTDIR`, `ELOOP`, a permission error, any other I/O error) halts
the same way an unreadable ancestor does above — never silently read as "does not exist
yet".

**Coordinate systems.** Every `publish.*` value is project-root-anchored (see "What
'Obsidian vault' implies" above); `<vault-root>` is the only vault-anchored quantity this
adapter computes, and the two coordinate systems are never mixed.

**Wikilink target prefix.** Once `<vault-root>` is known, this adapter derives one more
quantity, used only by the wikilinks-mode chapter-link and INDEX-target formulas
("Wikilinks vs Markdown links" and "INDEX wiring" below): `vaultRelChaptersDir =
relative(realpath(<vault-root>), realpath(publish.chapters_dir))`. Both operands are
realpath'd before the join, which is why a `chapters_dir` reached through a symlink into
a vault subdirectory still resolves to its true vault-root-relative position, never the
raw lexical path a naive `relative()` would produce. `currentIndexExpectedTarget`
(`assets/lib/chapter-paths.mjs`) is a pure helper with no filesystem access of its own;
this adapter is the fs-aware caller that computes `vaultRelChaptersDir` once per run and
passes it in — never a raw, un-realpath'd `publish.chapters_dir` value. An empty
`vaultRelChaptersDir` (`chapters_dir === <vault-root>`, the root topology) is a valid
result, not an error — see "Wikilinks vs Markdown links" below for what it produces.

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
```

Chapter slugs are **always English kebab-case** even when the prose is in another
language. The H1 and body render in `language.code`; only the filename and the URL-ish
slug stay English. This keeps the file tree greppable and the wikilink targets stable
across translations.

`group` is an optional field on a manifest entry (`references/manifest-discipline.md`),
also always English kebab-case, one level (no `/`). A manifest where no entry sets it —
the 1.4.1 shipped default — produces only the flat form above. As of 1.6.0, in
`assets/lib/chapter-paths.mjs`, `staticEmbedPath` (the asset-embed path formula, "Layout
you produce" below, now always the full-target join) and `validateGroups` (the
duplicate-slug halt, always runs) both now apply to group-free manifests; as of 1.8.0,
`currentIndexExpectedTarget`'s wikilinks branch is a third — a group-free manifest's
flat entry now emits `vaultRelChaptersDir/<slug>` ("Wikilinks vs Markdown links" below),
not the bare `<slug>` it emitted before 1.8.0. In `publish.wikilinks: false` mode this
adapter also changes group-free behavior further: the full-target glossary formula and
the Markdown-link integrity gate both now cover group-free manifests (see "Glossary
backlink discipline" and "Link integrity gate before you publish" below), and the
Related block's sibling/glossary links — including the ≥2 floor — are required in
Markdown form, not skipped (see "Wikilinks vs Markdown links" and "Chapter structure"
below). This list names the group-free changes we are aware of; it is not a claim that
every other section is unchanged. Flat and grouped entries coexist in one manifest.
Canonical chapter path (D2, shared with `static-md.md` and `SKILL.md`):

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

**The glossary AND chapter wikilinks share a different spelling with a different Quartz
sensitivity.** The embed climb above is about **relative-path depth**; the glossary link
(see "Glossary backlink discipline" below) and, since 1.8.0, the chapter wikilink (see
"Wikilinks vs Markdown links" below) are both vault-root-relative, and when Quartz's
`shortest` mode resolves either one at all, it does so via the **content-root-absolute**
fallback mentioned above — so their sensitivity is to a different relationship entirely:
the Quartz content root versus `<vault-root>`, not climb depth. The table below applies
identically to both link types, since both now resolve through the same
vault-root-relative coordinate.

| content root vs `<vault-root>` | behavior of the vault-root-relative wikilink (glossary or chapter) |
|---|---|
| **==** `<vault-root>` | resolves under `shortest` (`v4` via the root-absolute fallback, `v5` via multi-segment suffix) and under `absolute` |
| **⊊** `<vault-root>` (e.g. a `content/` subdirectory) | carries a stale leading prefix ⇒ does **not** resolve |
| **⊋** `<vault-root>` (the vault is nested inside the content root) | the target lacks the nesting prefix ⇒ **version-dependent**: fails under `v4` `shortest`/`absolute`, but `v5`'s multi-segment suffix matching may resolve it |
| disjoint, or the vault is not published through Quartz at all | the glossary is not on the site; **no** spelling repairs this |

It does **not** resolve under Quartz's `markdownLinkResolution: relative` — that mode
expects a source-relative target, the coordinate system the embed formula above uses,
not a vault-root-relative one.

**This conditionality does not undermine the choice.** This adapter's contract is
Obsidian (`publish.target: obsidian_vault`), and the vault-root-relative form is the
only spelling that resolves from every source note there. There is no spelling that is
universal once a Quartz content root differs from `<vault-root>` — keep the form, and
treat Quartz as a separately configured publishing constraint rather than inventing a
Quartz-content-root profile key.

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
- **The Related block ends every chapter** and contains ≥2 links to sibling chapters or
  glossary entries, in whichever form the profile dictates — see "Wikilinks vs Markdown
  links" below for the exact syntax, by target type, in each `publish.wikilinks` mode.
  With wikilinks on, this is also what makes the Obsidian graph view useful — a chapter
  with no outbound wikilinks is a graph island. Either way, you halt the publish step
  until at least two outbound Related-block links exist.

Start from `assets/chapter-template.md` and substitute the placeholders — never
hand-rewrite the skeleton from memory. Under `publish.wikilinks: false`, override the
template's `[[…]]` Related-block placeholders with the standard Markdown-link form from
"Wikilinks vs Markdown links" below — the template's Related section is written for the
wikilinks-on case only.

## INDEX wiring (do all of these on every chapter create/update)

These are the Obsidian-specific writes that turn a new `.md` file into a discoverable
chapter. Skip any of them and the chapter exists but no reader will find it. Item 2 is
the one exception to "do all of these" — see its own conditional note below.

1. **`{{publish.index_file}}`** — the section TOC. What "wire the chapter" means depends
   on whether the manifest entry sets `group` (`references/manifest-discipline.md`).

   **Flat entries** (no `group`, the 1.4.1 shipped case) — a flat entry never resolves a
   container, so wiring here is a membership check against one expected link target.
   A flat entry's expected link target uses `dirname(index_file)` — never
   `dirname(chapter_file)`, a different, chapter-relative coordinate system used
   elsewhere in this file: for `publish.wikilinks: false`,
   `relative(dirname(index_file), chapter_file)`; for wikilinks (the Obsidian default),
   the vault-root-relative chapter path (`.md` dropped) — `currentIndexExpectedTarget`'s
   **qualified** target (see "Wikilinks vs Markdown links" below). Path mode scans
   `{{publish.index_file}}` for a line matching that one target with `locateChapterLine`
   (the same helper the grouped Step 0 below calls) and branches on the match count:

   - **Two or more matches** — halt: a flat entry gets no special case here, the same
     duplicate halt fires exactly as it does for the grouped branch below.
   - **Exactly one match** — a flat entry has no container to verify, so that one line
     means this chapter is already wired; go straight to the link-integrity gate below.
   - **No match** — append a row for this chapter
     under whichever heading the file already uses for its flat chapter list; a flat
     entry never creates a container of its own. Order alphabetically by display title
     unless the existing file uses a different order — match what is there. The row's
     display text is always the manifest entry's `title`, never a slug or a hand-typed
     label.

   Wikilinks mode instead runs the qualified/legacy-bare **union scan** through
   `classifyChapterWiring` (`assets/lib/chapter-paths.mjs`) — see the "Step 0" bullet
   under Grouped entries below for the full algorithm. A flat entry has no container, so
   the four outcomes map directly onto the three bullets above, plus one new one: `absent`
   → append, same as "No match"; `duplicate` → the same "appears multiple times" halt as
   "Two or more matches"; `canonical` → already wired, same as "Exactly one match";
   `legacy` → retarget the matched bare-slug line to the qualified form in place,
   unconditionally — a flat entry has no container to be wrong about, so there is no
   placement check to run first here (unlike the grouped case below).

   Two worked examples (`publish.wikilinks: false`): `index_file` and the chapter share
   one directory ⇒ the target is the bare `<slug>.md`, no `../` climb; a repo-root
   `index_file` with chapters nested under `publish.chapters_dir` ⇒ the target is the
   full `chapters_dir`-prefixed path, e.g. `handbook/<slug>.md`.

   **Grouped entries** (`anyGroup` manifests) additionally resolve a container, so wiring
   runs a fixed sequence every time — first run and re-run alike:

   - **Step 0 — idempotency check.** Compute the expected link target: for standard
     Markdown links (`publish.wikilinks: false`), `relative(dirname(index_file), chapter_file)`;
     for wikilinks (the Obsidian default), the vault-root-relative chapter path (`.md`
     dropped) — `currentIndexExpectedTarget` returns `posixJoin(vaultRelChaptersDir,
     chapterRelPath(entry))` ("Vault root" above for `vaultRelChaptersDir`), the
     **qualified** target below.

     Path mode scans `{{publish.index_file}}` for a line matching that one target —
     `locateChapterLine` (`assets/lib/chapter-paths.mjs`) returns the match plus a
     structural `indexForm: 'headings' | 'non-heading'` field; key every branch below on
     `indexForm`, never on whether `containerTitle` is `null` — a `null` title occurs both
     for a genuinely non-heading file and for an uncontained match inside a headings-form
     file, and those two cases need different handling, below.

     Wikilinks mode instead runs a **union scan**: an installed handbook may still carry
     the pre-1.8.0 bare `[[<slug>]]` spelling for a chapter this run has not yet
     retargeted, so a single-target scan would silently double-append a qualified row next
     to an untouched legacy one. Compute the **legacy-bare** target too — `entry.slug` —
     and scan for both: `qScan = locateChapterLine(lines, qualified, {wikilink: true})`,
     `lScan = locateChapterLine(lines, legacyBare, {wikilink: true})` (the `{wikilink:
     true}` option folds one terminal `.md` off both sides of the comparison, so a
     hand-authored `[[handbook/admin/orders.md]]` or `[[orders.md]]` row still counts as a
     match, never a miss). Fold both scans through `classifyChapterWiring(qualified,
     legacyBare, qScan, lScan)` (`assets/lib/chapter-paths.mjs`) into exactly one of four
     outcomes — when `qualified === legacyBare` (the root-topology flat case,
     `vaultRelChaptersDir === ''` with no group) the two scans searched the identical
     string and are never double-counted:

     - `absent` (no line matches either target) — continue to container resolution below.
     - `duplicate` (two or more matching lines, in any combination of qualified and
       legacy-bare form) — never guess which line is canonical, halt:
       "Chapter '<slug>' appears multiple times in <index_file> — curate the index manually, then re-run."
     - `canonical` (exactly one line, already spelled in the qualified form) — the target
       string is present; run the placement check immediately below against that one line.
     - `legacy` (exactly one line, still spelled in the pre-1.8.0 bare-slug form) — the
       target is present under an old spelling; run the SAME placement check immediately
       below against that one line BEFORE touching anything — a misplaced bare line halts
       for manual relocation exactly like a misplaced qualified one, it is never retargeted
       first and relocated later.

     **The placement check is retained unchanged (D-8)** — `classifyChapterWiring` decides
     target-string presence and form only, never placement, so the pre-1.8.0 container gate
     still runs, layered on top of a `canonical` or `legacy` outcome, against whichever one
     line it selected:
     - `indexForm === 'headings'` and that line sits under a heading matching the entry's
       current `group_title` — compare via `containerTitleMatches(containerTitle, entry)`
       (titles compare TRIMMED, so a padded manifest title still converges) — correctly
       placed. A `canonical` line needs nothing further: wiring is already complete, go
       straight to the link-integrity gate below. A `legacy` line instead **retargets in
       place** — rewrite that line (the one `matches[0].line` identifies) from `[[<legacy
       slug>|Title]]` to `[[<qualified>|Title]]`, changing only its text, never its
       position.
     - `indexForm === 'headings'` and that line sits under a **different** heading, or is
       **uncontained** (`containerTitle` is `null` — the line sits above the file's first
       `##`, or after an H1 that resets the active container) — never silently relocate OR
       retarget a user-curated line, halt:
       "Chapter '<slug>' is listed in <index_file> under '<found_title>' instead of '<group_title>' — move the line (or curate the index manually), then re-run."
       (`<found_title>` reads "(none)" for the uncontained case.) **This halt fires
       identically for `canonical` and `legacy` (D-8):** a grouped chapter whose qualified
       wikilink is spelled exactly right but sits under the wrong heading is still a
       relocate-halt, not silently "already wired" — the 4-way classification answers
       presence and form only, it does not decide placement and it does not replace this
       gate. A `legacy`-form bare line under the wrong container also halts for relocation
       here, before any retarget is attempted — placement is checked before the in-place
       retarget, never after it.
     - `indexForm === 'non-heading'` (a nested list, an MkDocs-style YAML `nav:`, a bare
       path table, …) — no container to check placement against: a `canonical` line is
       already complete and a `legacy` line retargets in place unconditionally.
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

2. **`{{publish.glossary_seed}}` reconciliation (conditional)** — only when
   `publish.glossary_seed` is set and readable, confirm there is a `handbook` row with
   status `active` listing the section (add it if missing; flip `seed` to `active` once
   your first real chapter lands). Skip this item entirely when the key is unset — a
   vault with no seed index has nothing to reconcile.

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

- Internal chapter link: `[[<vault-rel>/<group>/<slug>|Display title]]` (`<group>`
  present only for a grouped entry; a flat entry's target is `<vault-rel>/<slug>`),
  where `<vault-rel>` is `vaultRelChaptersDir` ("Vault root" above), computed as
  `relative(<vault-root>, {{publish.chapters_dir}})` — the SAME vault-root-relative
  coordinate the glossary link below already uses, not the pre-1.8.0 bare `<slug>`
  basename form. Worked example (`vaultRelChaptersDir` `handbook`, entry `{group:
  'admin', slug: 'orders'}`): `[[handbook/admin/orders|Orders]]`. Root topology
  (`chapters_dir === <vault-root>`) collapses `<vault-rel>` to the empty string, so a
  flat entry's target is just `<slug>` — still the chapter's exact vault-root path (see
  "Vault root" above), never a special case.
- Glossary link: see "Glossary backlink discipline" below for the exact target.
- The pipe `|` separates target from display; omit it when display equals target.
- The target is vault-root-relative, never a bare basename — grouping DOES change it
  (the `<group>` segment rides on the joined path), unlike the pre-1.8.0 bare `<slug>`
  form. A bare slug only disambiguates when it is unique across the WHOLE vault; this
  skill enforces uniqueness only across the handbook
  (`references/manifest-discipline.md`), so a same-basename foreign vault note could
  shadow it — and under `publish.per_group_slug_uniqueness` that guarantee narrows to
  within-group only, so two different-group chapters may share a slug, and a
  user-authored bare `[[slug]]` link can no longer disambiguate them: the caveat this
  opt-in accepts. The vault-root-relative form resolves Obsidian's exact full-path tier
  instead, unambiguous regardless of what else shares the chapter's basename elsewhere
  in the vault.

`publish.wikilinks: false`:

- Internal chapter link, any manifest — every chapter the skill WRITES (new chapters, and
  chapters a manual-migration rewrite touches) uses the full-target formula (write-time
  canon (see "Write-time canon" in `revalidation.md`); retained chapters keep whatever
  spelling they already have — the link-integrity gate below checks that the target
  resolves, not that the spelling matches this formula):
  `[Display title](relative(dirname(chapter_file), <target-chapter-file>))`. For a
  group-free manifest, linking and target chapters share one directory, so this formula
  naturally evaluates to `<chapter-slug>.md` — the same spelling as the shipped 1.4.1
  form, not a special case.
- Glossary link: see "Glossary backlink discipline" below.
- Skip Dataview blocks; they require Obsidian to render.

You do not mix the two styles in one chapter. The profile decides; the chapter follows.

**Transition note (pre-1.8.0 handbooks).** A chapter this run does not touch keeps whatever
wikilink spelling it already has — established behavior, `references/revalidation.md`'s
"Write-time canon". An untouched NESTED chapter's bare `[[<slug>]]` link resolves through
Obsidian's fragile suffix tier (tier 5, §0a) — it works today only as long as no foreign
vault note shares the basename. An untouched ROOT-level chapter's bare `[[<slug>]]` already
resolves through the exact-match tier (tier 3) and needs no fix. The next publish, or a
material revalidation, that touches a nested chapter upgrades it to the vault-root-relative
form ("INDEX wiring" above, the union scan's `legacy` outcome).

## Glossary backlink discipline

Every domain term's **first occurrence** in a chapter links to its glossary entry. The
glossary itself lives at `{{publish.glossary_dir}}/index.md` and is owned by
`references/glossary-discipline.md` — this adapter only encodes the linking syntax:

- Wikilinks on: `[[<vault-rel>/index#TermHeading|TermHeading]]`, where `<vault-rel>` is
  `relative(<vault-root>, {{publish.glossary_dir}})` — vault-root-relative, **not** the
  basename form 1.6.0 shipped. Worked example (vault root `vault/`, `glossary_dir:
  vault/knowledge/glossary`): `[[knowledge/glossary/index#Term|Term]]`.
- Wikilinks off, any manifest — every chapter the skill WRITES uses the full-target
  formula (write-time canon (see "Write-time canon" in `revalidation.md`); retained
  chapters keep whatever spelling they already have, per the link-integrity gate below):
  `[TermHeading](relative(dirname(chapter_file), {{publish.glossary_dir}}/index.md)#termheading)`.

Two wrong spellings shipped in 1.6.0 for want of this rationale, so record it: the raw
`publish.glossary_dir` path (project-root-anchored, the wikilinks-on form 1.6.0 shipped)
is unresolvable as a wikilink target — worse, clicking it in Obsidian **creates a bogus
file** inside the vault; the bare-basename form resolves in Obsidian only through the
non-segment-aware last-resort suffix tier, and under **no** Quartz mode at all.

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
   The resolved target must also stay inside `<vault-root>` (see "Vault root" above) —
   halt if `capture.output_dir` resolves outside it (e.g.
   `capture.output_dir: screenshots` from a chapter at `vault/handbook/foo.md` resolves
   to `../../screenshots/…`, outside the vault, so the embed is broken and unportable).
   This containment check applies unchanged at any group depth, and it applies equally
   under `publish.wikilinks: false` — the glossary link there is filesystem-relative too
   (see "Glossary backlink discipline" below), so this is the adapter-wide "inside the
   vault" contract, not a wikilink-syntax concern. Unlike the static-Markdown target,
   `capture.output_dir` is **not** required to sit under `publish.chapters_dir` —
   sibling vault subtrees resolve fine as long as the target stays inside `<vault-root>`.
   `capture.output_dir` deliberately plays no part in selecting `<vault-root>` itself —
   it is validated only here, which keeps this check meaningful: widening which
   `.obsidian/` marker counts as the root could otherwise paper over a capture
   destination that has drifted outside the vault.
2. Every wikilink target (`[[…]]`) resolves to either an existing `.md` file in the
   vault or an existing heading anchor in the glossary. Broken wikilinks render as
   red placeholders in Obsidian and are silent in plain Markdown views. When
   `publish.wikilinks: false`, this item also verifies every **relative** standard
   Markdown link (`[text](target)`) resolves to a real file the same way — every
   manifest, group-free manifests included: grouped chapters can sit at different
   depths, so a stale or hand-edited relative link is exactly as broken as a dangling
   wikilink and must be caught here too. A bare-fragment target (`[text](#heading)`,
   no path component) is checked against the **current chapter's own headings**, not
   the vault or the glossary. A `mailto:` link, an `http://`/`https://` link, or any
   other non-relative target (a URI scheme, or a vault-rooted/absolute path) is
   **exempt** — this item verifies vault-internal resolution, not that an external
   link is reachable.
   **This gate is chapter-scoped**: it fires here, before declaring the chapter
   published, so it catches a legacy broken link only when that chapter is next
   published, or revalidated in a way that **touches** it — an accepted-diff refresh
   or a material re-author (`references/revalidation.md`). A **no-op** revalidation
   classifies the chapter unchanged and never runs this gate. It does not sweep untouched chapters
   — an already-published chapter with a stale link stays broken until a publish, or a touching
   revalidation, next runs against it.
3. The chapter has ≥2 outbound links in its Related block (outbound-link floor).
4. The frontmatter `language` matches `language.code`; the section labels match
   `publish.section_labels.*` verbatim.
5. `{{publish.index_file}}` lists the chapter — under its `group_title` container for a
   grouped entry, or under its flat chapter-list heading for a flat one (both per "INDEX
   wiring" above); when `{{publish.glossary_seed}}` is set and readable, its `handbook`
   row is marked `active` — this half of item 5 is skipped when the key is unset.

A chapter that fails any of these is unpublished, not "almost done" — fix and re-verify.
