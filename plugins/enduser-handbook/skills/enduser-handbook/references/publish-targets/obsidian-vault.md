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
you produce" above, now always the full-target join) and `validateGroups` (the
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
wait for the co-located-assets follow-up issue (#222), which keeps a grouped chapter's assets
in the same directory as the chapter so no `../` climb is ever needed. This is documentation
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
     file, and those two cases need different handling, below. Branch on match count
     first, before keying on `indexForm` at all: **zero** matches ⇒ continue to container
     resolution below; **exactly one** ⇒ proceed to the placement check immediately below
     against that one line; **two or more** ⇒ never guess which line is canonical, halt:
     "Chapter '<slug>' appears multiple times in <index_file> — curate the index manually, then re-run."
     — the same wording the wikilinks union scan's `duplicate` outcome below uses.

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
       path table, …) — call `verifyNonHeadingPlacement(indexLines, selectedTarget, group_title,
       {wikilink: publish.wikilinks})` (`assets/lib/chapter-paths.mjs` — the option is the
       profile's own mode, mirroring the mode-correct chapter link below, never hardcoded;
       `selectedTarget` = whichever of the qualified or legacy-bare target that one matching line
       carried), checking placement BEFORE any retarget, exactly as the headings-form branch
       above does, and branch on the result:
       - **`ok`** ⇒ a `canonical` line is already complete and a
         `legacy` line retargets in place unconditionally.
       - **`unverifiable`** ⇒ proceed — this file falls outside the verified class (see
         "Nested-list automation limits" below); the check ran and could not conclude — nothing
         further verifies placement, no confirmation is requested, and the run continues
         unverified, exactly as the shipped 1.10.0 behaviour did on this path — a `canonical`
         line is already complete and a `legacy` line still retargets in place unconditionally.
         See the safety statement under "Non-headings index, no existing line" below for what
         that does and does not guarantee.
       - **`misplaced`** ⇒ halt reusing the exact wording above:
         "Chapter '<slug>' is listed in <index_file> under '<found_title>' instead of '<group_title>' — move the line (or curate the index manually), then re-run."
         (`<found_title>` reads "(none)" when the line sits at the left margin, uncontained.)
       - **`inconsistent`** ⇒ a defensive contradiction check, not an outcome any documented
         caller reaches: Step 0's own two-or-more halt — the wikilinks union scan's `duplicate`
         outcome above, and the matching two-or-more halt path mode now runs (see "Path mode
         scans" above) — already guarantees exactly one line matches `selectedTarget` before this
         branch is ever reached, in either mode, so re-scanning that same target here should
         reproduce that same single match — this fires only if some future caller, or a change to
         the matching logic, ever breaks that invariant. Never guess which match is canonical, halt:
         "Chapter '<slug>' does not resolve to exactly one line in <index_file> — curate the index manually, then re-run."
       This replaces the shipped 1.10.0 "no present-line placement verifier runs here" behaviour
       for the verified class only; every other non-heading file still proceeds unverified,
       unchanged.
   - **Container resolution** (headings-form index — resolved by heading here; a bounded
     nested-list index is instead wired by `wireNestedListChapter`, "Non-headings index"
     below). Look for a heading whose text equals the entry's `group_title` — containers are
     located by title, never by the English `group` slug:
     - Zero matches — create one (`## <group_title>`, at the heading depth the file
       already uses for its top-level sections), then append the chapter line under it.
     - Exactly one — append the chapter line under it, respecting whatever ordering
       convention the file already follows.
     - More than one — halt:
       "Found multiple '<group_title>' containers in <index_file> — curate the index manually, then re-run."
   - **Non-headings index, no existing line.** Attempt automated nested-list wiring: call
     `wireNestedListChapter(indexLines, group_title, <mode-correct chapter link>)`
     (`assets/lib/chapter-paths.mjs`). The chapter link is the profile's own mode-correct
     form — under `publish.wikilinks: false` a Markdown link `[Display title](<target>)`,
     under `publish.wikilinks: true` a wikilink `[[<target>|Display title]]` — whose `<target>`
     is the very spelling step 0 computed above for this profile
     (`relative(dirname(index_file), chapter_file)` in path mode;
     `currentIndexExpectedTarget`'s vault-root-relative qualified target in wikilinks mode).
     The function owns list structure only and treats the link as opaque. Branch on the result:
     - `{kind: 'inserted', newLines}` — the index was a bounded nested-list container form;
       persist the returned `newLines` (joined back, they reproduce the exact bytes — EOL and
       terminal newline preserved) and proceed — the container was found or created and the
       chapter line inserted under it.
     - `{kind: 'present', index}` — a child bullet under the resolved container already carries
       this run's own chapter link, verbatim, though step 0 (`locateChapterLine`) still reports
       the chapter absent: only the chapter's own title can cause that split, by keeping step
       0's target-parse from ever recognizing the row the writer already wrote. This check
       compares literal content, never a parsed target, so it catches exactly what step 0
       cannot; `index` is diagnostic only and is never interpolated into the halt below — an
       existing follow-up tracks halt-text injection through found-row text as its own defect,
       and this halt does not repeat it.
       One halt text serves both `publish.wikilinks` modes — it names no link syntax, and the
       adapter reaches the outcome in either mode when the exact child row uses `-` and its title
       keeps step 0 from parsing the target. (`wireNestedListChapter` itself will also return
       `present` for an ordinary exact child if called directly, but this adapter's step 0 already
       handles that row and never calls it.) The title rule differs by syntax. In path mode an
       unescaped `]` closes the label early, while `\]` leaves the target resolvable. In wikilinks
       mode an interior `]` followed by more alias text keeps `WIKILINK_TARGET_RE` from reaching
       the row's closing brackets; a terminal run of `]` can instead be consumed as part of those
       closers and the target still resolves. A backslash does not escape `]` for that regex.
       Thus `A]B` reaches this halt after one `-` child is written in either mode, while a
       wikilink alias ending in `A]` does not. When the emitted child marker is `*` or `+`, a
       target-breaking grouped row reaches the re-read refusal below before anything is written,
       rather than `present`. A target-side route (a slug whose own text ends in `.md`, since
       `currentIndexExpectedTarget` strips one terminal `.md` in wikilinks mode) converges the
       same ordinary way:
       `Chapter row for '<slug>' is already present under the '<group_title>' container bullet in <index_file>, but this run could not recognize it — the chapter's own title does not yield a resolvable link destination. Give the chapter a plain title in the manifest — no Markdown markup, backslash escapes, or HTML entities in it — then re-run; see "Nested-list automation limits" below.`
     - `{kind: 'unwritable', field}` — [1.11.0] before returning, the writer re-reads the exact
       bytes it is about to hand back through its own reader (`prepareIndexLines`,
       `hasYamlMappingStructure`, `containerOwnerScan`); if that reader would refuse them,
       nothing is written and this outcome fires instead of `inserted`. `field` — `'title'`,
       `'group_title'`, or `'unknown'` when substituting either stand-in still fails to clear
       the rejection — names the manifest value at fault, computed by substitution (swap the
       emitted line for a known-good stand-in and re-read) rather than by inspecting the
       value, so it stays correct for causes nobody has enumerated yet. Deliberately
       conservative: it can decline a value that would in fact have round-tripped safely,
       which is the right direction for a tool rewriting a file it does not own. Render
       `<remedy>` from `field`. `'unknown'` is reachable only when no single emitted line's
       replacement clears the rejection, i.e. both values are independently at fault.
       `group_title` is GROUP-scoped — `validateGroups` requires every entry of a group to
       carry the same value — so a remedy naming only THIS chapter does not converge: the next
       run halts on the conflicting-`group_title` gate instead. The three renderings:
       - `'title'` ⇒ `Give this chapter a plain title in the manifest.`
       - `'group_title'` ⇒ `Give a plain group_title to EVERY entry of this chapter's group in the manifest — it is group-scoped, so changing it on this chapter alone halts on the conflicting-group_title gate instead.`
       - `'unknown'` ⇒ `Give this chapter a plain title, and a plain group_title to EVERY entry of its group — group_title is group-scoped, so changing it on this chapter alone halts on the conflicting-group_title gate instead.`

       One halt text for both `publish.wikilinks` modes — verified, not assumed: the wording
       names no link syntax, and the remedy it asks for is safe under both modes, which matters
       because the fatal SHAPES differ by mode (measured: a backslash-escaped `]` in a title is
       written in path mode and refused in wikilink mode on `*`/`+`):
       `Cannot wire '<slug>' into <index_file>: the lines this run would write are not recognizable to the next run, so nothing was written. <remedy> Then re-run. For this recovery step, use a non-empty value made only of Unicode letters and numbers, with words separated by single ASCII spaces. That positive constraint is deliberately narrower than the parser's full accepted language; it was verified across both link modes and all three bullet markers of the line being written, regardless of markers elsewhere in the file. See "Nested-list automation limits" below for the measured per-marker set.`
     - `{kind: 'multiple'}` — two or more container bullets match `group_title`; never guess
       which is canonical, halt:
       "Found multiple '<group_title>' container bullets in <index_file> — curate the index manually, then re-run."
     - `{kind: 'not-a-list'}` — the index is not in the automatable nested-list subset (see
       "Nested-list automation limits" below): a YAML `nav:`, a bare path table, or a list
       shape outside the bounded safe subset. **Verify the named form before naming it — a
       halt is convergent only if the exact pair it prescribes would actually be recognized on
       the very next run, so check that, rather than promise it:**
       1. build the candidate as its own two-line array: `- <group_title>`, then on the next
          line the profile's own mode-correct chapter link, indented two spaces under it —
          `publish.wikilinks: true`: `  - [[` + target + `|` + title + `]]`;
          `publish.wikilinks: false`: `  - [` + title + `](<` + target + `>)` (destination
          inside angle brackets — the same destination-wrapping the "Non-headings index, no
          existing line" bullet above already uses for this profile's mode-correct chapter
          link — and any `]` in the title escaped as `\]`, the same escaping the
          `publish.wikilinks: false` halt text below promises, so the gate validates the exact
          spelling the operator is told to type);
       2. run `locateChapterLine(<candidate>, <index-relative-target>, {wikilink:
          publish.wikilinks})` (`assets/lib/chapter-paths.mjs`) on that two-line array alone,
          not the real index — the option is the profile's own mode, mirroring the
          mode-correct chapter link above, never hardcoded (the same rule "The placement check
          is retained unchanged (D-8)" above states for the shipped verifier call);
       3. run the fixed-probe writer predicate on the same array —
          `wireNestedListChapter(<candidate>, group_title, <fixed probe link>)`;
       4. **the candidate's own text, split on newlines, is exactly two physical lines — not
          merely two array elements, since an embedded newline inside `title` or `target` can
          add more; exactly one match; that match's `matches[0].index === 1`
          (`LocateChapterLineMatch.index`, `assets/lib/chapter-paths.mjs`) — the indented
          chapter line, never the container line at index 0; and the predicate returning
          `{kind: 'inserted'}`** — the pair is representable — emit the convergent halt naming
          it exactly, in the profile's own mode:

          `publish.wikilinks: true` (Obsidian default):
          `Index <index_file> is not a headings-form file — add a '<group_title>' container and the chapter line for '<slug>' manually, then re-run. The next run recognizes the chapter line as a Markdown list row INDENTED TWO SPACES under the '<group_title>' container bullet, whose wikilink target is exactly '<index_relative_target>' — that is, a '- ' + group_title line followed by a '  - [[' + target + '|' + title + ']]' line; a Markdown link whose destination is that target plus '.md' is recognized too. Give the row a plain title — no Markdown markup, backslash escapes, or HTML entities in it — or the next run may not be able to confirm its placement; see "Nested-list automation limits" below for exactly what is recognized.`

          `publish.wikilinks: false`:
          `Index <index_file> is not a headings-form file — add a '<group_title>' container and the chapter line for '<slug>' manually, then re-run. The next run recognizes the chapter line as a Markdown list row INDENTED TWO SPACES under the '<group_title>' container bullet, whose link destination is exactly '<index_relative_target>' — that is, a '- ' + group_title line followed by a '  - [' + title + '](<' + target + '>)' line, with the destination inside angle brackets and any ']' in the title escaped as '\]'. Give the row a plain title — no Markdown markup, backslash escapes, or HTML entities in it — or the next run may not be able to confirm its placement; see "Nested-list automation limits" below for exactly what is recognized.`
       5. **anything else** — the gate rejects the pair — measured causes include an ordinary
          newline inside the title, a delimiter the chosen mode's link syntax cannot carry
          (wikilinks mode: `|`/`#`/`^`/`]` in the target — see "Wikilinks vs Markdown links"
          below), and a `group_title` the writer's own bullet grammar refuses (padded with extra
          whitespace, or carrying markup). Emit the plain, unchanged 1.10.0 halt instead, with no
          named form and no convergence claim:
          "Index <index_file> is not a headings-form file — add a '<group_title>' container and the chapter line for '<slug>' manually, then re-run."
          (Double-quote delimited, unlike the backtick-delimited halts above — this delimiter is
          load-bearing and pinned: a reference-assets test's fallback needle matches this
          string's closing double quote, so do not "normalize" it to backticks.)
          The operator is no worse off than before 1.11.0 here — this halt can repeat verbatim
          on the next run, exactly as it always has. The gate never names a pair that would not
          converge, but it also never claims convergence it has not checked. Read on even when
          the candidate pair converges — one more operator-actionable warning applies
          regardless of this gate's outcome, described next.

       **Leaving the gate above: one further warning applies whether or not the candidate pair
       converges.** Item 4's convergence promise holds — but only for a `group_title`, target
       and title the gate accepts: you halt once with instructions, the user adds the container
       and the chapter line, and the re-run's step 0 finds the line present under the
       `indexForm: 'non-heading'` branch above and proceeds. One operator-actionable warning
       belongs here too, and it is narrower than "markup in the title": it applies to a title
       whose markup keeps the row's own link target from resolving — a nested link, a nested
       image, a reference link, or an interior `]` followed by more title text (a
       `- [A]B](<target>)` path-mode row, or a `- [[target|A]B]]` wikilink alias — both measured
       to make the target unrecognizable to `locateChapterLine`, exactly like a nested link
       would). Escaping the bracket fixes this in path mode (`- [A\]B](<target>)` resolves); a
       backslash does **not** escape it for `WIKILINK_TARGET_RE`, so
       `- [[target|A\]B]]` still fails. The wikilink rule is positional, not "any `]`": when
       the title ends in a run of `]`, that run can be consumed as part of the row's closing
       brackets and the target resolves.

       Convergence depends on the manifest entry's own `title` — not on whatever row already
       sits in the index — because that is what the writer rebuilds its inserted row from on
       every run. If an existing row (operator-typed, or left over from any prior run) does not
       resolve but the manifest title is clean, the writer's own insert resolves immediately:
       the earlier, unrecognizable row lingers beside it as a cosmetic duplicate, and the very
       next run reports `ok` on the clean one. That is the one combination that converges with
       a harmless leftover; the other combination — the manifest title ITSELF target-breaking —
       does not converge the same way. Its result turns on the marker of the child row the writer
       is about to emit, not a file-wide or container-wide marker: it reuses the last existing
       child's marker and falls back to the container marker only when there is no child. Link
       mode still decides which title spellings break step 0, as described above.

       - **Nested under its single matched container** (the ordinary case — where
         `wireNestedListChapter` always places its own insert): every later run still finds
         `containers.length === 1`, and step 0 still reports the chapter absent — the row is
         exactly as unrecognizable to step 0's target parse as before — but when the new child
         uses `-`, the writer's own membership guard (the `present` outcome above) recognizes its
         own prior insert VERBATIM, refuses to write a second copy, and halts instead. Exactly
         ONE row is ever written here, in either mode, on that marker; the shipped 1.10.0
         behaviour this retires had no membership check at all and appended another duplicate
         row on every re-run, without limit. A new child using `*` or `+` does not reach this
         guard at all — see "Measured, across every placement" below for what it hits instead.
       - **At the left margin (indent 0), uncontained** — a broken title's own brackets fail
         the indent-0 `isPlainLabel` check the same way regardless of what broke them or which
         mode wrote them, so `containerOwnerScan` declines the WHOLE scan (`{kind:
         'not-a-list'}`) for every container in the file, not just this row. Nothing is ever
         inserted from here: the plain, unnamed `not-a-list` halt (the "anything else" branch
         above) just repeats verbatim, forever — zero duplicates form, not one — measured in
         both modes (a target-breaking wikilink alias at the left margin declines the scan
         exactly like a target-breaking Markdown-link title does).

       A `group_title` that is itself non-plain reaches that same zero-growth `not-a-list` halt
       by a third, independent path, also mode-independent: `wireNestedListChapter` checks the
       group axis before it ever looks at containers or existing rows, so a malformed
       `group_title` short-circuits there regardless of the chapter title, the row's placement,
       or `publish.wikilinks`.

       Measured, across every placement × mode × title-resolvability combination that matters
       here, holding the manifest entry's `title` FIXED across runs (the next paragraph lifts
       that condition): a row that already resolves inserts nothing further; a stale row
       alongside a clean current manifest title gives one lingering duplicate then `ok`; the
       same target-breaking title sitting at the left margin, or a non-plain `group_title`,
       converges on zero further rows and a repeating `not-a-list` halt instead (unchanged
       since 1.10.0), in either mode, regardless of marker. A target-breaking current title
       nested under its container is the one outcome that splits by the EMITTED ROW'S MARKER,
       not just mode: when that marker is `-`, it converges on exactly one row, then a `present` halt from the
       second run onward, in either link mode (new in 1.11.0 — this is the case #330 retires
       from unbounded, on that marker). When that marker is `*` or `+`, the identical
       placement does not reach `present` at all — but the cause is the TARGET, not the
       broken title by itself: `parseNestedLabel` falls to its `raw` branch on the same
       broken title, and that raw text is the row's WHOLE unparsed content, including the
       link target. `wireNestedListChapter` is reached only for a GROUPED entry, and a
       grouped entry's target always carries its group prefix (`chapterRelPath`,
       `chapter-paths.mjs:168-172`, returns `<group>/<slug>.md` whenever `group` is set) —
       that `/` is what trips `isBarePathBullet` ("Nested-list automation limits" below),
       not the broken title in isolation: measured, an otherwise-identical flat-style,
       slash-free target reaches `present` instead, so do not generalize this to "a
       target-breaking title is always unwritable on `*`/`+`". For THIS adapter's own
       grouped emission the prefix is unavoidable, so the split above holds in practice: the
       [1.11.0] re-read postcondition (`{kind: 'unwritable', field}`, "Non-headings index, no
       existing line" above) is what the `*`/`+` child reaches here, because
       `isBarePathBullet` fires on the very bytes the writer is about to persist — so nothing
       is ever written and the run halts naming `field: 'title'`, before the membership check
       #330 adds ever gets a chance to run. On that marker the pre-existing
       `isBarePathBullet` guard (unchanged since 1.10.0) is exactly what the postcondition now
       runs pre-emptively, so #330 changes nothing there — the guard already answered
       `not-a-list` before this release; the postcondition is what turns that into a named,
       never-written refusal instead of a lockout discovered a run later.

       Every claim above holds the manifest `title` FIXED between runs — none of it was
       measured against an EDITED one. When the operator instead edits a target-breaking title
       while the emitted child uses `-` (a new title yields a new display string, hence a new
       `chapterLink` the membership guard has never seen), that guard cannot recognize the
       edited row as the same chapter and inserts it as a fresh child every time: measured, 20
       publishes with the title edited on every fourth run accumulate 5 rows — one per distinct
       edit, none ever removed. The run is not silent: the other 15 publishes each return
       `present` and the adapter halts on it. No halt names the orphaned rows, though, which is
       what leaves the growth unreported. The `present` bound above is per TITLE
       STRING, not per chapter — it is bounded only by how many times the title is edited, a
       count this file has no way to bound.

       Separately: a title that merely renders non-plain while its target still resolves — an
       ampersand,
       emphasis, an HTML entity (see "Nested-list automation limits" below for the measured
       table) — which is found and simply left unverified at the left margin (measured in both
       `publish.wikilinks` modes), but is `ok` if instead correctly nested under its container,
       since `isPlainLabel` is never applied to a child bullet, only to an indent-0 one. That
       harmlessness does NOT extend to a backtick or an HTML comment in the same nested
       position: `isPlainLabel` genuinely never touches a child bullet, but a
       backtick — any run length, paired or not — or `<!--` inside one still reaches `stripInertContexts` on the
       very next scan — the file-wide sanitizer-identity check (`prepareIndexLines` step 6,
       `assets/lib/chapter-paths.mjs`), not `isPlainLabel`, is what fires, and it degrades the
       WHOLE file, not just this row, to `not-a-list` from that point on (measured; see
       "Nested-list automation limits" below for the worst case). A bare backslash escape is
       mode-dependent, not an example of either case: measured at the left margin, `A\.B`
       returns `unverifiable` under `publish.wikilinks: true` (the wikilink form never decodes
       the escape) but `misplaced` — a halt — under `publish.wikilinks: false` (the
       markdown-link form decodes it, so the row reads as a plain, uncontained bullet). Use a
       plain-text title to avoid any of this.

       The gate above is checked on the candidate's own isolated two-line array. **By
       construction, that proves only that the candidate pair is well-formed and would be
       recognized on its own —
       it proves nothing about the real index, because it never reads the real index.**
       Any property of the real file that makes the shipped locator or writer
       decline can still diverge from what the isolated check found; the cases below are
       measured illustrations, not a closed list:
       - an inert region (a fenced code block, an HTML comment) blanks a representable pair, so
         it is reported absent again — repeating the convergent halt above, never completing;
       - a chapter row that exists only inside leading frontmatter is reported present by the
         shipped locator and reaches `unverifiable` in the present-line branch above — the check
         ran and declined to conclude (see the safety statement below for what that does and
         does not guarantee); the adapter proceeds unverified (the shipped 1.10.0 writer/locator
         view disagreement, tracked separately as #337 — see "Nested-list automation limits"
         below);
       - a real index whose surroundings carry YAML structure, a wildcard, or an ordered list
         makes the writer decline the whole file on the next run too: once the pair is present,
         step 0 routes to the present-line branch above, whose own predicate call declines the
         same way, and the adapter again proceeds on `unverifiable` rather than a repeated halt.

       A future case diverging some other way is expected, not a defect in this documentation —
       the isolated check was never designed to rule any of this out.

       **The honest safety statement, scoped to what this PR governs: on the non-heading branch above,
       this gate never lets a MISPLACED row complete silently when it can verify placement.**
       Wherever it cannot conclude — the check never runs, because the line was never even
       reported present, or it runs and returns `unverifiable` — the run falls back to the same
       unverified completion named just above: it is not that a false completion cannot occur
       there, and not that every way it can occur is named above.

       **The headings branch is unchanged by this PR and already completes silently:** a
       chapter row inside a valid frontmatter block whose body itself carries a heading sits
       under a matching container per the headings-form placement check above
       (`indexForm === 'headings'`, "The placement check is retained unchanged (D-8)") and
       completes with neither verification nor confirmation — the same shipped 1.10.0
       writer/locator view disagreement named above, tracked separately as #337.

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

**Automated grouped wiring covers a Markdown-headings-form index and a bounded nested-list
(GitBook `SUMMARY.md`) container subset.** A headings-form index resolves its container via
"Container resolution" above; a non-heading index whose shape falls inside that bounded subset
(see "Nested-list automation limits" below) is wired by `wireNestedListChapter` per the
line-absent branch above. Every other non-heading index form — an MkDocs YAML `nav:` block, a
bare path table, or any list shape outside the safe subset — stays fully manual: you halt with
the non-heading instructions above and stop there. First-class YAML `nav:` container automation
remains its own follow-up, #328. Path-table container automation, by contrast, is not merely
deferred: it was decided against as not soundly automatable — see #340 for the recorded
reasoning.

### Nested-list automation limits

`wireNestedListChapter` automates only a **bounded, conservative** nested-list subset and
defers everything else to the manual `not-a-list` halt above — safety over reach. It wires an
index only when it is a plain bullet list whose container labels **and** the entry's
`group_title` are plain-text: it refuses any label or `group_title` carrying inline markup or
a leading block trigger — emphasis, a link inside the visible text, an image, raw HTML, an
entity, a **bare** backslash escape, inline code, a leading `#` heading or list marker,
or a run of collapsing whitespace —
because a character allowlist cannot prove such a label renders equal
to a plain `group_title`, so matching it could miss a real container or manufacture a
duplicate. The escape refusal applies to the label's raw, literal spelling only: a
whole-content markdown link wrapper is unwrapped and its escape decoded before the
plain-label check ever runs, so `Admin\.X` written bare is refused while the same escape
written as `[Admin\.X](x.md)` decodes to the plain `Admin.X` and is accepted. A whole-content
wikilink wrapper is unwrapped too, but its escape is left undecoded — `[[Admin\.X]]` still
carries the literal backslash and is refused exactly like the bare form. Matching decodes a
backslash escape only for the markdown-link form — not what the label renders as in full,
since an HTML entity is never decoded either way (below); a wikilink's escape is never
decoded, so its label is compared on its literal source spelling instead.
It also refuses a `*`- or `+`-marked bullet whose visible text is a **bare
(non-link) path** — one containing a `/` or backslash separator, or ending in `.md` — because
the shipped membership scan only sees `-`-marked bare rows, so wiring such a file could create
a second container beside a retained phantom row (a legitimate `*`/`+` plain label that happens
to contain `/` is refused too, a deliberate over-rejection, not corruption). Inline code, an
HTML comment or a fenced block anywhere, a mixed or bare-CR line ending, a YAML `nav:` or
`- key: value` mapping bullet, a list nested more than one level deep, and a multiline
`group_title` fall outside the subset as well. Worst case for the residual (a file the guards
above decline) is a cosmetic duplicate container an operator might introduce by hand while
following the manual halt instructions — visible and deletable, never data loss. Within the
automated subset itself, the writer's target-breaking-title outcome follows the marker of the
child it is about to emit. With `-`, a title shape that defeats step 0 converges on exactly one
chapter row, then halts rather than writing a second (see "INDEX wiring" above for the
title-EDIT growth caveat). The shipped 1.10.0 writer's unbounded per-re-run growth is retired
for that marker. With `*` or `+`, the [1.11.0] re-read postcondition instead returns (`{kind:
'unwritable', field}`, "Non-headings index, no existing line" above): the row's own raw
fallback text carries the GROUPED target's `/` — not the broken title by itself (see
"Measured, across every placement" above for the group-prefix mechanism and the flat-target
counter-example) — which trips `isBarePathBullet` on the very bytes about to be persisted, so
the writer refuses before writing anything and halts naming `field: 'title'`, rather than a
hand-typed bare path. A richer rendering-aware matcher is a possible follow-up, not a bug.

A manifest value that would corrupt this same structural read is refused the same way, before
it is ever written. The set is per-field, and the two fields do not share it:

- In a **chapter title**, on any emitted child marker: a backtick — any run length, paired or not, since the
  mechanism is an unterminated inline code SPAN and not a fence; a tilde run is measured
  harmless — an HTML comment, or a U+2028/U+2029 separator. When the child marker is `*` or
  `+`, the path-mode row also falls to raw content after an unescaped `]` or a trailing odd run
  of backslashes. In wikilinks mode its whole-content label unwrap falls to raw content for any
  `]` in the alias, escaped or not; the grouped target's `/` then trips `isBarePathBullet`.
  This writer-side unwrap rule is broader than step 0's `WIKILINK_TARGET_RE`: step 0 still
  recognizes a terminal run of `]` as part of the closers. When the child marker is `-`, the
  writer permits these bracket/backslash shapes; only those that also defeat step 0 lead to the
  later `present` halt.
- In a **`group_title`**: a U+2028/U+2029 separator on any newly-created container marker; when
  that marker is `-`, a first token followed immediately by a colon (`FAQ: basics`, `Admin:`)
  or a value that is only hyphens (`---`); when it is `*`/`+`, a value containing `/` or ending
  `.md` (`'Sales/Marketing'`, `'billing.md'`). On creation this marker is copied from the first
  indent-0 bullet, not inferred from a file-wide style.

Each of those reaches `{kind: 'unwritable', field}` — see "Non-headings index, no existing line"
above for the exact halt text — never a written row. Inline code, an HTML comment and a run of
several backticks are deliberately absent from the `group_title` list: measured 9/9 across `-`,
`*` and `+`, those short-circuit to `{kind: 'not-a-list'}` before any write is attempted, so they
surface as the manual halt and never as `unwritable`. Note what that rules out: a backtick run in
a `group_title` is never a fence. The container line is emitted as the marker, a space, then the
value, so the run cannot start its own line — and in these cases the value never reaches emission
at all. Recover with a value in the halt's own recovery class — Unicode letters and numbers,
single ASCII spaces between words — applied to the chapter, or to EVERY entry of its group, then
re-run. The plain-label predicate named below is BROADER than that class and satisfying it alone
can meet the identical halt again.

**The plain-label predicate, named exactly.** In short: a plain title is verified; a
non-plain title that still resolves is found but left unverifiable; a title that breaks its
own row's link target is caught — by the writer's own membership guard when the emitted child
uses `-` (a `present` halt), or by the [1.11.0] re-read postcondition when it uses `*`/`+`
(an `unwritable` refusal naming the title) — rather than duplicated without limit on either
marker (see the marker-scoped bounded-outcome discussion under INDEX wiring above). The
mechanism: the container-owner scan
(`containerOwnerScan`,
`assets/lib/chapter-paths.mjs`) applies `isPlainLabel` to whatever `extractLabel` returns for a
row's own content — never to the row's raw source text, and never to what it renders as in
Obsidian — and it applies this check to EVERY indent-0 bullet in the file, not only the row
under test: a single non-plain indent-0 label anywhere in the file declines the WHOLE scan
(`{kind: 'not-a-list'}`), so an otherwise-clean 'Admin' container elsewhere in the file cannot
rescue a badly-labelled row sitting at the left margin. `extractLabel`'s own decoding differs by
the label's link syntax, and this is the load-bearing half for THIS adapter, whose default is
`publish.wikilinks: true`: a whole-content markdown link decodes backslash escapes before the
check runs (`[A\.B](x.md)` becomes the plain `A.B`), but a whole-content **wikilink alias does
NOT** decode them (`[[x|A\.B]]` keeps its literal backslash and stays non-plain) — the identical
escape spelling behaves differently depending on which link syntax the profile's mode writes. An
HTML entity is never decoded by either form, so a title that LOOKS plain once rendered in
Obsidian can still fall outside the verified class in both modes. Measured for a row sitting AT
THE LEFT MARGIN alongside a clean, correctly-formed 'Admin' container elsewhere in the same
file, in both modes:

| Row source                        | Mode      | `extractLabel`      | `isPlainLabel` | Verdict          |
|------------------------------------|-----------|----------------------|----------------|------------------|
| `- [A.B](<items.md>)`              | path      | `A.B`                | true           | `misplaced`      |
| `- [A\.B](<items.md>)`             | path      | `A.B`                | true           | `misplaced`      |
| `- [A&#46;B](<items.md>)`          | path      | `A&#46;B`            | false          | `unverifiable`   |
| `- [A & B](<items.md>)`            | path      | `A & B`              | false          | `unverifiable`   |
| `- [A *b*](<items.md>)`            | path      | `A *b*`              | false          | `unverifiable`   |
| `- [See [here][ref]](<items.md>)`  | path      | *(target never resolves)* | —         | absent at step 0 |
| `- [[items|A.B]]`                  | wikilinks | `A.B`                | true           | `misplaced`      |
| `- [[items|A\.B]]`                 | wikilinks | `A\.B`               | false          | `unverifiable`   |
| `- [[items|A&#46;B]]`              | wikilinks | `A&#46;B`            | false          | `unverifiable`   |
| `- [[items|A & B]]`                | wikilinks | `A & B`              | false          | `unverifiable`   |
| `- [[items|A *b*]]`                | wikilinks | `A *b*`              | false          | `unverifiable`   |
| `- [[items|A]B]]`                  | wikilinks | *(target never resolves)* | —         | absent at step 0 |

The last row of each mode is a different failure mode entirely: a nested link (path mode) or an
unescaped `]` in the alias (wikilinks mode) breaks the row's OWN link-target extraction — not
`extractLabel`/`isPlainLabel` at all — so step 0 never reports the chapter present in the first
place; see the marker-scoped bounded-outcome discussion under INDEX wiring above for what a
target-breaking title does instead: one inserted row, then a `present` halt when the child uses
`-`, or an `unwritable` refusal — naming the title, nothing ever written — on a
`*`/`+` child — never SILENT unbounded duplication for a fixed title, on either marker.

As of 1.11.0, a **present** grouped chapter's placement under this container is also checked,
but only for a narrow verified class — this exact sentence, reused verbatim everywhere it is
cited (see `revalidation.md`'s "Terminal-state convergence checklist" and the 1.11.0 CHANGELOG
entry):

files for which the fixed-probe writer call returns `kind === 'inserted'` or `kind ===
'present'` and which hold exactly one selected-target match, that match lying outside the
writer-recognized leading-frontmatter span.

**In practice:** this is the subset above, minus a selected target that resolves to zero lines
or to more than one (`inconsistent` — see "INDEX wiring" above) and minus a match sitting inside
leading frontmatter (`unverifiable` — the shipped 1.10.0 view disagreement, below). Operators
land on `unverifiable` rather than inside the verified class most often for one of: a Markdown
nav file using a wildcard, an ordered list, or an explicit `<!--nav-->` marker (all ordinary
`mkdocs-literate-nav` features); two same-named containers; a chapter row sitting inside leading
frontmatter; or a **native/YAML MkDocs `nav:` configuration**, which gets no placement
verification at all (see the safety statement above under "Non-headings index, no existing
line") — the run completes unverified, exactly as before 1.11.0, with no confirmation
requested. First-class YAML `nav:` container automation remains its own follow-up, #328.

Three disclosures the operator is owed, not proved away:

- A `SUMMARY.md` holding more than one Markdown list — `mkdocs-literate-nav` honors only the
  *last* one, while this machinery scans indent-0 bullets across the whole file, so a row can
  verify against a list the tool ignores. The shipped writer already carries this exposure;
  1.11.0 does not widen it.
- A bullet-only file that also happens to be valid YAML — an `ok` now verifies placement where
  1.10.0 completed silently with no check at all: a Markdown-reading answer about bytes some
  other consumer may read as YAML.
- A chapter row sitting inside leading frontmatter is never verified **on the non-heading
  branch above** — it returns `unverifiable` there, for the reason below, not because it was
  overlooked. On the headings branch (unchanged by this PR), a frontmatter block whose body
  itself carries a heading is a different, unfixed gap: it completes with neither
  verification nor confirmation — see the safety note in "INDEX wiring" above.

**An index whose frontmatter poisons the view is a known defect, filed as #337 — not fixed
here.** The writer's own body-preparation view blanks a leading frontmatter block before
wiring, while the step-0 locator's view does not, so the two sides can disagree about what a
frontmatter-embedded chapter line means. On a nested-list index this produces both a false
"already wired" report and a chapter line that duplicates on every subsequent run (the shipped
1.10.0 frontmatter bug, #337). `verifyNonHeadingPlacement` above only stops this case from
returning a false `ok` — a match inside the span returns `unverifiable` instead — it does not
repair the duplication.

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
  within each namespace — every group, plus the flat group-less set, is its own
  namespace — so two different-group chapters may share a slug, and likewise a flat
  chapter and a grouped one, so a user-authored bare `[[slug]]` link can no longer
  disambiguate the pair either: the caveat this opt-in accepts. The vault-root-relative
  form resolves Obsidian's exact full-path tier instead, unambiguous regardless of what
  else shares the chapter's basename elsewhere in the vault.

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
   (see "Glossary backlink discipline" above), so this is the adapter-wide "inside the
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
