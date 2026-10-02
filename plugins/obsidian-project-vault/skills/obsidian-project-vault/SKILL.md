---
name: obsidian-project-vault
description: Set up, migrate, or operate an Obsidian vault as an LLM Wiki — a persistent, compounding knowledge base maintained by Claude Code. Three-layer architecture (raw sources, wiki, schema), setup modes (create, migrate, audit), ongoing operations (ingest, query, lint, merge). Covers vault/ subfolder setup, .gitignore for .obsidian/, vault MCP config, INDEX.md navigation, Report template with frontmatter, CLAUDE.md workflow integration, data-dump handling, migration from standalone vault with diff-before-delete safety, structural audit, source ingestion workflows, query-and-file-back, and ongoing maintenance. Triggers on "set up obsidian", "create vault", "migrate vault", "audit vault", "obsidian documentation", "project knowledge base", "obsidian for project", "LLM wiki", "wiki-lint", "lint wiki", "check vault health", "audit docs", "wiki health", "ingest", "process sources", "wiki-ingest", "update wiki from sources", "merge wiki pages".
---

# Obsidian Project Vault — LLM Wiki

Obsidian as a git-tracked, LLM-maintained knowledge layer. Not generic Obsidian advice — specifically: Obsidian + Claude Code + git, following the LLM Wiki pattern.

For human-side workflow tips (Web Clipper, graph view, Dataview queries), see `references/obsidian-tips.md`.

## The Pattern

Most LLM-document workflows are RAG: retrieve chunks, re-derive answers from scratch every query. Nothing accumulates. The LLM Wiki inverts this — the LLM **incrementally builds and maintains a persistent wiki** between the human and raw sources. New sources get integrated (entity pages updated, summaries revised, contradictions flagged), not just indexed. Knowledge compounds with every source added and every question asked.

### Three Layers

**Raw sources** — immutable collection of source documents (articles, papers, data files, transcripts, PDFs). LLM reads from these but never modifies them. Source of truth. Location: outside the vault (e.g. `research/`, `raw/`, `data/` — project-specific). The point is separation: raw sources are inputs to the wiki, not part of it.

**Wiki** — LLM-generated markdown in `vault/<knowledge>/`. Summaries, entity pages, concept pages, comparisons, synthesis, cross-references. LLM owns this layer entirely — creates pages, updates them when new sources arrive, maintains cross-references, keeps everything consistent. Human reads and browses in Obsidian (graph view, search, backlinks).

**Schema** — `CLAUDE.md` + vault conventions. Structure rules, frontmatter contracts, workflow definitions. Human and LLM co-evolve this as patterns emerge. This is what makes the LLM a disciplined wiki maintainer rather than a generic chatbot.

Schema should specify:
- Directory structure and naming conventions
- Frontmatter fields required per page type (the "contract")
- How INDEX.md and log.md are formatted
- What happens during ingest (which pages to create/update)
- Cross-reference conventions (wikilinks, typed properties)
- What belongs in the wiki vs what stays as raw source
- Where source ingest state lives and how Ingest and Lint resolve it (see Source ingest state below)

### Source ingest state

Raw source contents stay immutable, including Markdown. For every new source, keep mutable bookkeeping in an adjacent sidecar named by appending `.meta.md` to the **complete filename**: `research/paper.pdf.meta.md`, `data/results.csv.meta.md`, `raw/interview.txt.meta.md`, or `raw/article.md.meta.md`. This distinguishes `paper.pdf` from `paper.csv`. Reserve the `.meta.md` suffix for source metadata; do not use it for raw documents.

Example `research/paper.pdf.meta.md` before ingest:

```yaml
---
type: source-state
ingested: false
---
```

After successful ingest, set `ingested: true` and `ingested_date: YYYY-MM-DD` in that sidecar. The sidecar path identifies the source; never append YAML to a PDF, CSV, transcript, or other raw payload.

Ingest and Lint MUST use the same rules:

1. Enumerate raw source files recursively under the directory configured in the project's CLAUDE.md, regardless of format. Exclude `.meta.md` sidecars and any non-source paths explicitly excluded by that schema.
2. If the exact adjacent sidecar exists, read its YAML frontmatter as the authoritative state. It takes precedence over legacy inline state, even if they disagree. Do not fall back when a sidecar is malformed or unreadable.
3. Otherwise, for an existing Markdown source only, honor a legacy YAML frontmatter `ingested` boolean. Read this state without modifying the raw file; future ingest writes a sidecar.
4. A source with no recorded state or `ingested: false` is **pending**. Only the YAML boolean `ingested: true` is **processed**; strings such as `"true"` are invalid. Missing `ingested` in an existing sidecar, invalid values, malformed frontmatter, and unreadable metadata are **state errors**, never proof of processing. Report these separately and leave those sources unprocessed until the state is corrected.
5. Count each raw source once, never its sidecar. Report sidecars whose corresponding raw source is missing as orphan metadata, not as ingest inputs.

For an existing vault, use these rules immediately: PDFs/data files/transcripts without sidecars are pending; legacy Markdown `true` remains processed and `false` remains pending. Create a false sidecar when registering a new source, or during ingest for a pending source without one. Do not infer completion from an existing wiki page. Record the raw directory, exclusions, sidecar convention, and legacy fallback in the project's CLAUDE.md.

Before marking a source processed, verify that its raw payload is already committed in the project repository, or available through a durable shared external source store documented in CLAUDE.md (including retrieval and local path mapping so citations resolve in another checkout). Register newly added, untracked raw files in a separate source-only commit per project conventions before the wiki/state commit; committing a raw file does not change its contents. If source availability cannot be established, leave it pending and report the required registration rather than committing a processed sidecar for a local-only file.

### Roles

The human curates sources, directs analysis, asks the right questions, and thinks about what it all means. The LLM does the grunt work — summarizing, cross-referencing, filing, maintaining consistency, updating indexes. The bookkeeping that makes a knowledge base actually useful over time but that humans abandon because it's tedious.

**"Obsidian is the IDE; the LLM is the programmer; the wiki is the codebase."**

---

## Mode

1. **No vault exists** → Setup
2. **Standalone vault exists outside project** → Migration
3. **Vault exists in project** → Audit

---

## Setup

### Structure: vault/ subfolder

Never make the project root the vault — Obsidian graph/search shows src/, target/, scripts, JSON.

```
project/
├── raw/ or research/ or data/  ← immutable source documents (outside vault)
├── vault/                      ← Obsidian points here (the wiki)
│   ├── Dashboard.md            ← Dataview overview + [[INDEX]] link
│   ├── <knowledge>/            ← name to match domain (reports/, decisions/, docs/, etc.)
│   │   ├── INDEX.md            ← all sections, status, gaps
│   │   ├── log.md              ← append-only activity record
│   │   └── <section>/
│   ├── templates/
│   │   └── <NoteType>.md
│   └── sources/                ← optional metadata about external sources
├── .gitignore
├── .mcp.json                   ← vault MCP, ABSOLUTE path
└── CLAUDE.md                   ← schema: structure, conventions, workflows
```

Choose folder names that match the project's domain. Examples: `reports/` for research, `decisions/` for ADRs, `runbooks/` for ops, `features/` for product specs.

### .gitignore

```gitignore
vault/.obsidian/workspace.json
vault/.obsidian/workspace-mobile.json
```

Track everything else in .obsidian/ (plugin configs, app.json). No Obsidian Sync if using git — two sync mechanisms fight.

Pre-flight: verify vault has no binary files (`find vault/ -name '*.png' -o -name '*.pdf'`).

### .mcp.json

npx mcpvault needs ABSOLUTE path:
```json
{
  "mcpServers": {
    "vault": {
      "command": "npx",
      "args": ["-y", "@bitbonsai/mcpvault@latest", "/absolute/path/to/vault"]
    }
  }
}
```

Replace the vault path with the project's absolute path. If `.mcp.json` already
exists, add the `vault` entry inside its existing `mcpServers` object and preserve
the other servers.

### Optional: kepano/obsidian-skills

Install [kepano/obsidian-skills](https://github.com/kepano/obsidian-skills) for native Obsidian syntax support (Bases, Canvas, wikilinks, CLI). 20K stars, maintained by Obsidian's creator.

### CLAUDE.md integration

Add to project instructions:
```markdown
- **Read `vault/<knowledge>/INDEX.md` first** before starting any work
- **Write notes directly** to `vault/<knowledge>/<section>/` with frontmatter
- **Tabular data**: >100 rows of raw results → summary in vault, full data elsewhere
- **Ingest new sources** following the Ingest workflow (see Operations below)
- **File valuable answers back** into the wiki as new pages
- **Run lint periodically** to check wiki health
```

Schema checklist — CLAUDE.md should also specify:
- Which directory is raw sources, which is wiki
- Frontmatter contract per page type (required fields, allowed values)
- Which page types evolve, which are stable, and which date records substantive content updates
- Ingest conventions (what page types to create, how to cross-reference)
- Source ingest state contract above (sidecars, missing-state discovery, legacy Markdown fallback)
- When to file query answers back vs leave in chat

### Note template

The project's CLAUDE.md is the single frontmatter contract for Setup, Audit,
Ingest, Query, Merge, and Lint. Keep an existing vault's per-type schema; do not
impose a second field set or silently rename its properties. If no contract
exists, record this default in CLAUDE.md before creating notes:

- Wiki notes require `type`, `section`, and `date` (the note's authored/published
  date, in `YYYY-MM-DD` form). `tags` is an optional list.
- Evolving types require `updated` in `YYYY-MM-DD` form. Start with `report`,
  `research`, `idea`, `decision`, `experiment`, `synthesis`, and `query-result`;
  stable types such as `architecture`, `algorithm`, and `guide` do not need it.
  The default stale threshold is 90 days. Adapt the type lists and threshold to
  the domain and record them in CLAUDE.md.
- `updated` records the last substantive content change. Initialize it when
  authoring a new evolving note; advance it when facts, analysis, or conclusions
  change. Reading, linting, adding navigation links, or repairing metadata alone
  must preserve it. An unknown historical date stays unresolved, never today.
- `title`, `status`, `created`, and `aliases` are optional unless the project's
  per-type schema requires them. Use the filename as the display title when
  `title` is absent. Separate contracts cover navigation, logs, templates,
  generated audit reports, and source-state sidecars; they are not wiki notes.

For imported existing notes with unknown authoring dates, report the missing
date as unresolved rather than filling today. A newly authored wiki note uses
its actual authoring date; this is separate from its raw source's publication date.

`vault/templates/<NoteType>.md`:
```yaml
---
type: report
section:
date:
updated:
tags: []
---
```

Fill `section`, `date`, and `updated` when creating the report; the template's
empty placeholders are not a completed note. Adapt fields to the declared
per-type schema, and use that same contract in every operation below.

### INDEX.md

`vault/<knowledge>/INDEX.md`: table of all sections with status, coverage, last-updated, gaps.

The LLM reads INDEX.md first when answering queries — it's the primary navigation hub. At moderate scale (~100 sources, hundreds of pages), index + drill-down works surprisingly well without embedding-based RAG.

Scale guidance:
- **Small wikis (<50 pages)**: can include per-page entries with one-line summaries
- **Larger wikis**: keep section-level with per-section sub-indexes when 10+ files
- Sections with 1-3 files don't need their own index

### log.md

`vault/<knowledge>/log.md`: append-only chronological record of wiki operations.

Advantage over `git log`: readable in Obsidian, queryable via vault MCP, shows wiki-level operations (ingest/query/lint) not file-level diffs. A git log entry says "updated 5 files"; log.md says "ingested article X, updated entity pages for A, B, C, flagged contradiction with existing claim in D."

Each entry:

```markdown
## [YYYY-MM-DD] verb | Subject
One-line summary. Affected pages: [[Page1]], [[Page2]], [[Page3]].
```

Verbs: `ingest`, `query`, `lint`, `restructure`, `create`, `merge`.

Parseable with unix tools: `grep "^## \[" log.md | tail -10` for last 10 entries.

### Dashboard.md

Dataview queries + wikilink to INDEX: `See [[INDEX|Full Index]] for status.`

Dataview paths are vault-relative (`FROM "reports"`). They break on structural changes — verify after restructuring.

---

## Migration

### Pre-flight

```bash
git checkout -b vault-merge
tar czf /tmp/vault-backup.tar.gz /path/to/vault
```

### Move

```bash
mv /path/to/standalone-vault project/vault/
```

### Diff before delete — CRITICAL

If project has files overlapping with vault, compare contents BEFORE deleting. Produce
3 lists using relative paths under the corresponding knowledge folders, not basenames.
If the layouts differ, record the project → vault path mapping explicitly.

| List | Action |
|------|--------|
| In BOTH | Diff each pair and verify the vault preserves all project content as described below. |
| ONLY in project | Copy to vault first without overwriting an existing file; verify the copy before deleting the project file. |
| ONLY in vault | Safe, no action. |

For **every In BOTH pair**:

1. Run `diff -u -- "project/path/note.md" "project/vault/path/note.md"` with
   the actual mapped paths and read the entire diff. Exit 0 means identical; exit 1
   means different and requires review; exit >1 is a comparison error — halt for
   that pair and keep both copies.
2. For different files, explicitly verify that the vault is a **content superset**:
   every project-side fact, paragraph, frontmatter value, link, and unique edit is
   preserved. Extra vault frontmatter/tags or a longer/newer file alone is not proof
   of enrichment. Inspect both files in full when the diff is insufficient.
3. If project-only content is missing, merge it into the vault while preserving
   vault-only content, then repeat the diff and content review. If edits conflict
   or preservation is uncertain, **keep both copies and halt deletion for that pair
   for human review**; never choose a winner by filename, timestamp, or file size.
4. Record both paths, the comparison result (identical / verified superset /
   merged and verified / unresolved), and the evidence for preservation in the
   migration report. Delete only the individual project file whose current vault
   copy passed verification. If either copy changes after review, compare again.

Never bulk-delete an overlapping project directory while any file is unresolved or
unverified. A missed file **or a unique edit in a same-named file** is data loss.

### Update paths

Grep old vault path across project. Common locations:
- Scripts → `Path(__file__).resolve().parent.parent / "vault"` (project-relative)
- `.mcp.json` → absolute path to vault/
- `CLAUDE.md` → update path refs and note counts (verify against actual filesystem — stale docs lie)
- Memory files → update path refs

### Evaluate sync scripts

Script that copied source → vault?
- **Just copies** → retire. Reports authored directly in vault now.
- **Enriches** (frontmatter, tags, dates) → keep, refactor for in-place enrichment on vault/.

### Repoint Obsidian

Open another vault → Open folder as vault → `project/vault/`

---

## Audit

Initial structural check when first adopting a vault. For ongoing content health, see Lint under Operations.

### Navigation

Test: Dashboard → INDEX.md → section → note. Can "what do we know about X?" be answered in ≤2 reads?

### Dead content

| Issue | Check |
|-------|-------|
| Empty folders | Accumulate after restructuring. Delete. |
| Dead templates | Reference deleted vault structure. Replace or delete. |
| Broken wikilinks | `[[Target]]` pointing to renamed/deleted notes. Grep for `\[\[` and verify targets exist. |
| Orphan files | Metadata/source files nothing links to. |
| Duplicate folders | Spelling variants (section-a/ vs section_a/). Merge or delete empties. |

### Frontmatter

Sample 10 wiki notes across sections and page types. Validate the required fields,
types, and allowed values against the project's CLAUDE.md contract established
in Note template above; flag missing or empty required values. Exclude navigation,
logs, templates, generated audit reports, raw sources, and state sidecars from
wiki-note requirements. If property names diverge across notes (e.g. `rating` vs
`score`), formalize the schema rather than inventing a different Audit minimum.

### File sizes

No line limits — 300-line analysis is fine. Distinguish by content type:
- **Narrative analysis** (any length) → vault
- **Raw tabular dumps** (>100 rows of search results) → summary in vault, full data elsewhere

### Dashboard

Dataview queries must match vault structure. Queries on deleted/renamed folders = errors.

### .gitignore

workspace.json excluded? No sensitive data tracked? Binary files handled?

---

## Operations

The three ongoing workflows that make the wiki compound. Setup gives you infrastructure; Operations give you the flywheel.

### Ingest

Process raw source documents into the wiki. The vault's CLAUDE.md should define where raw sources live (e.g., `sources/`, `raw/`, or outside the vault entirely).

Arguments:

- No args: interactive mode (default) — process one source at a time with discussion
- `--batch`: process all unprocessed sources with minimal prompting

#### Phase 1: Scan for unprocessed sources

Enumerate sources and resolve state using Source ingest state above. Queue every pending source, including files without metadata. Report state errors and orphan metadata separately; process unaffected pending sources, but do not ingest sources with state errors until corrected. Report "no unprocessed sources" only when the complete scan has neither pending sources nor state errors; a failed or incomplete scan must be reported as such.

#### Phase 2: Read and discuss (interactive mode)

For each unprocessed source:

1. **Read** the full raw source file, using a format-appropriate reader (e.g. PDF extraction or a data-file reader), not the sidecar. If it cannot be read adequately, report the limitation and leave it pending; do not synthesize from metadata alone.
2. **Summarize** the key takeaways in 3-5 bullet points
3. **Ask the user**:
   - What aspects are most important?
   - Should this create a new wiki page or update existing ones?
   - What directory should new pages go in? (suggest based on content)
4. **Identify** which existing wiki pages this source relates to:
   - Search INDEX.md for related topics
   - Grep for mentions of key concepts across the vault
   - Compare candidate pages' titles, aliases, normalized names (case, spacing, punctuation), and
     content before choosing a target. Similar names are candidates, not proof of the same entity or
     topic; inspect scope and source evidence before updating an existing page or creating a new
     one.
   - List the pages that should be updated

In `--batch` mode: skip the discussion and determine target pages and emphasis using the same
duplicate check. If entity identity or topic scope is ambiguous, report the candidate paths for
human review and defer that source rather than create a competing page or combine distinct entities;
leave its ingest state pending.

#### Phase 3: Process into wiki

For each source, do ALL of the following:

**A. Create or update wiki pages:**

- If the source introduces a new topic → create a new page using the appropriate template
- If the source adds to an existing topic → update the relevant existing page(s)
- When updating, add new information clearly marked with context: what's new, what it changes
- If new information contradicts existing claims, note the contradiction explicitly — record both claims, which source says what, your current assessment. Don't modify the source.

**B. Cross-link bidirectionally:**

- Every new/updated page MUST have a `## Related` section
- Link to at least 2 existing pages
- Update those pages' `## Related` sections to link back
- If a page doesn't have a `## Related` section yet, add one

**C. Cite the source:**

- In new/updated wiki pages, reference the source with a relative link back to the raw file
- This creates a trail from wiki content back to raw sources

**D. Prepare source state:**

For a pending source without a sidecar, create the adjacent `.meta.md` with `type: source-state` and `ingested: false`. Preserve existing sidecar fields. Never edit legacy inline frontmatter or raw source contents. Keep the source pending until wiki, index, and log updates succeed (Phase 4).

**E. Update frontmatter on modified wiki pages:**

- Follow the page type's CLAUDE.md schema. Initialize its content-update date on
  creation and refresh it when this ingest changes substantive content. Preserve
  dates on pages changed only to add backlinks or repair metadata.

#### Phase 4: Log and report

1. Append to log.md using the log contract:
   `## [YYYY-MM-DD] ingest | {source title}`, followed by a one-line summary and
   `Affected pages: [[Page1]], [[Page2]].`
2. Update INDEX.md if new sections or directories were created
3. Only after the source was adequately read, its raw payload availability was verified under Source ingest state, and its wiki, index, and log updates succeeded, set `ingested: true` and `ingested_date: YYYY-MM-DD` in its sidecar. On failure, leave it pending and report partial work for reconciliation on retry.
4. Report to user: sources processed, sources still pending or with state errors, pages created, pages updated, key insights

#### Phase 5: Commit

```bash
git add -- {changed-wiki-index-log-paths} {changed-source-sidecar-paths}
git commit -m "Ingest: {source title or 'N sources'}"
```

Stage the exact sidecars created or changed in this ingest even when they are outside the vault; verify both wiki updates and source state are staged together. Do not stage raw payloads or unrelated files as part of this operation.

Push per project conventions — do not auto-push unless CLAUDE.md specifies it.

#### Quality checks

Before committing, verify:

- [ ] All new pages have `## Related` with 2+ links
- [ ] Required per-type metadata is populated; content-update dates reflect substantive changes, while navigation-only edits preserve them
- [ ] Successfully processed source's sidecar marked as `ingested: true` with `ingested_date`; incomplete sources remain pending
- [ ] Raw source contents unchanged; changed sidecars included in the commit
- [ ] Raw payload already committed separately or retrievable from the documented shared source store; source links resolve after checkout/retrieval
- [ ] Entry appended to log.md
- [ ] No broken links introduced (quick grep check)
- [ ] Markdownlint passes on new/modified files

#### Key principles

- Sources are immutable. The wiki synthesizes; sources record.
- Prefer one-at-a-time ingest with human review for important sources. Use batch ingest for lower-stakes volume.
- Develop your preferred ingest workflow over time and document it in the schema (CLAUDE.md).

**Page types** (adapt to domain):

- **Entity pages** — people, places, organizations, products — whatever the domain's nouns are
- **Concept pages** — themes, methods, patterns, ideas
- **Source summaries** — one per ingested source, linking to entity/concept pages it touches
- **Synthesis pages** — cross-cutting analysis, comparisons, timelines
- **Query results** — valuable answers filed back from conversations (see Query below)

### Query

Answer questions from the compiled wiki, not from raw sources:

1. **Navigate** — Read INDEX.md to find relevant pages
2. **Read** — Drill into those pages for detail
3. **Synthesize** — Combine information from multiple pages into an answer, citing wiki pages as sources

Answers can take different forms: a markdown page, a comparison table, a JSON Canvas (visual knowledge map), a chart. For Canvas and Marp slide syntax, see kepano/obsidian-skills if installed.

**The key insight: file valuable answers back into the wiki.** A comparison you asked for, an analysis, a connection you discovered, a synthesis that required reading many pages — these are valuable. They shouldn't disappear into chat history. Create a new wiki page (type: synthesis or query-result) so the insight compounds in the knowledge base just like ingested sources do.

When to file back vs leave in chat:
- **File back**: any answer that took significant synthesis, that you might reference again, or that reveals a non-obvious connection
- **Leave in chat**: quick factual lookups, transient questions, things already covered by existing pages

Use the page type's declared frontmatter contract when filing a query result
back. Update INDEX.md and append to log.md using
`## [YYYY-MM-DD] query | {question or subject}`, followed by a one-line summary
and links to affected pages.

### Merge

Consolidate wiki pages after human review establishes that they cover the same entity or topic. A
duplicate finding alone does not justify a merge; namesakes and intentionally separate scopes stay
separate.

1. **Choose the canonical page.** Read each candidate in full, including frontmatter, citations,
   headings, and block IDs; check identity and scope against source evidence. Choose the retained
   path using the project's schema and navigation conventions, recording why. Do not choose by
   filename, age, or size alone. Record donor paths and inventory their unique contributions before
   editing. Resolve donor outbound links, embeds, and citation paths in their original locations;
   record the actual target paths and fragments, not just their written destinations.
2. **Preserve content and evidence.** Fold donor facts, analysis, citations, and relevant properties
   into the canonical page. Keep conflicting claims attributed to their sources with their
   uncertainty intact; do not silently choose the newer claim. Preserve meaningful former names as
   schema-supported aliases after checking collisions. Retain referenced headings/block IDs, or
   record an old-fragment → new-fragment mapping when they change, including ID collisions. Resolve
   property conflicts explicitly; keep both pages if preservation remains uncertain. Recompute
   copied Markdown destinations and reference definitions using Lint's link conventions in the
   canonical context (relative or vault-root paths), so they still point to the recorded targets.
   Resolve copied wikilinks and same-note fragments there too;
   disambiguate or map them explicitly if their meaning changes after moving. Raw payloads
   and source ingest state stay unchanged.
3. **Retarget incoming links.** Use Lint's Link Integrity inventory below across the vault,
   configured raw-source directories (read-only), and any other incoming-link locations declared in
   CLAUDE.md. Include Markdown links/embeds and reference definitions, wikilinks/embeds, path
   variants, display aliases, and heading/block fragments. Rewrite only references resolved to a
   donor; calculate replacement paths using the referring file's declared link convention and
   preserve display text, embeds, and
   verified fragment mappings. Update writable INDEX, Dashboard, Related, and audit references.
   Never rewrite immutable raw sources or append-only historical log entries. For their incoming
   references, retain the donor or a schema-supported redirect/archive at its original path that
   preserves referenced headings, block IDs, and content needed by embeds; an alias alone does not
   preserve an old path. Report retained donors and ambiguous/unreadable references.
4. **Verify before deletion.** Compare the canonical page against the donor inventory: every unique
   contribution and citation must survive. Resolve rewritten links and their fragments, check the
   canonical page's outbound links against their recorded original targets and fragments, and
   verify no alias became ambiguous. A different existing file is not the same citation. Delete only
   individual
   donor wiki files with no remaining incoming references and complete preservation evidence. Keep
   donors whenever a check is incomplete; repeat content and link checks after deletion. Report
   intentional redirects/archives separately, using exclusions only if declared in the schema.
5. **Log and review.** Append `## [YYYY-MM-DD] merge | {canonical page}` with a one-line summary and
   `Affected pages:` links to retained pages. Record removed donor paths as code spans, the
   canonical choice, and fragment mappings or retained donors. Refresh the canonical page's
   schema-defined freshness field only for substantive content changes; mechanical
   incoming-link/metadata edits preserve other pages' dates. Review the full diff and confirm raw
   payloads, source state, and unrelated work are unchanged. Stage only the exact canonical, donor,
   incoming-link, index, and log paths changed by this merge (`git add -- {changed-paths}`), inspect
   the staged diff, and commit/push per project conventions.

### Lint

Periodic content health-check. Distinct from Audit (one-time structural check) — Lint is ongoing maintenance that keeps the wiki accurate and alive as it grows.

Arguments:

- No args: full audit (all checks)
- `--quick`: only broken links and orphans (fast)
- `--fix`: auto-fix all fixable issues without prompting

#### Checks

Run ALL checks below. Use Grep and Glob tools for efficient scanning — do NOT read every file in full.

Wiki-page checks and `--fix` apply only to wiki pages: exclude raw sources and their state sidecars even if the configured source directory is inside the vault. Inspect source state only through the Knowledge Gaps check below; never apply wiki frontmatter defaults to raw files or sidecars.

**1. Link Integrity**

Broken internal links:

- Scan wiki Markdown for both Markdown links/embeds (`[text](target)`, `![text](target)`, and
  reference-style links with their definitions) and wikilinks/embeds (`[[target]]`, `![[target]]`).
  Grep is discovery, not a complete parser: distinguish real links from examples in code, and
  inspect multiline or escaped syntax when a scan cannot resolve it.
- Separate a wikilink's display alias (`[[target|label]]`) from its destination; keep its heading
  (`#Heading`) or block (`#^block-id`) fragment. For Markdown destinations, account for URL
  encoding, optional titles, and fragments without confusing them with the file path. Skip external
  URLs in this internal check.
- Resolve both Markdown destinations and wikilinks using the actual Obsidian conventions recorded
  in the project's schema. Explicit `./` or `../` relative paths start at the referring file;
  vault-root path formats start at the vault root. Obsidian also supports shortest unique paths.
  If the convention or destination is unclear, verify with the actual resolver or report it as
  unverified; do not assume every Markdown path is relative. Never declare a folder-qualified
  `[[folder/Name]]` valid merely because an unrelated `other/Name.md` exists.
  For unqualified names or shortest-path candidates, enumerate
  candidates; if there are multiple matches, verify with the actual Obsidian resolver or report
  ambiguity rather than pick the first basename. A frontmatter alias alone is not proof that
  `[[Alias]]` resolves; verify any alias used as a destination with the resolver. Verify the
  resolved target file exists, including attachments or raw files cited by wiki pages; inspect
  without editing raw sources.
- Verify heading/block fragments in the resolved note, including same-note links such as
  `[[#Heading]]` or `[section](#heading)`. An existing file does not make a missing fragment valid.
  If the resolver or fragment rules cannot be established, report resolution as unverified rather
  than broken or healthy.
- Report broken, ambiguous, or unverified references with referring file, line, original
  destination, resolved path or candidate paths, and reason. Reuse this resolved link inventory for
  connectivity and merge checks; never count plain-text mentions or display labels as links.

External URL candidates:

- Grep for `](http` patterns
- Flag URL candidates in notes older than 6 months using the schema's declared authored date (`date`
  by default); if that date is absent or invalid, report its age as unknown. A note's age does not
  establish a URL's age or availability.
- Report as "may need verification" — do NOT fetch URLs

**2. Page Connectivity**

Orphan pages (no inbound links from wiki notes or navigation):

- Build a set of wiki `.md` candidates, excluding INDEX.md, Dashboard.md, README.md, log.md,
  templates, attachments, raw sources and their sidecars, and the vault's `audits/` directory
  (including previous lint reports).
- Use the Link Integrity inventory to count resolved incoming links/embeds from other wiki notes
  and navigation pages (including INDEX, Dashboard, and navigational README files) to each
  candidate's actual path; a self-link is not an inbound link. Exclude operation logs, generated
  audit reports, raw sources, state sidecars, templates, and attachments as connectivity sources.
  Report their references separately: bookkeeping links do not establish navigable connectivity.
  Keep those references in the full integrity/merge inventory to protect their destinations.
- Pages with zero inbound links are orphans only when the incoming-link scan is complete. Report
  ambiguous or unverified references separately instead of using them to prove connectivity or
  orphanhood.

Missing backlinks (A → B but B ↛ A):

- For each file with a `## Related` section, extract its outbound links
- Resolve those targets with the same inventory and check whether each linked wiki page has a
  resolved link back. Report unresolved targets separately; raw files and attachments do not require
  wiki backlinks.

Hub pages (10+ inbound links):

- Use the same wiki/navigation connectivity sources as the orphan check above.
- Report as informational, not an issue

**3. Content Freshness**

Stale pages:

- Only check evolving page types (e.g., research, ideas, decisions, experiments) — skip stable types (e.g., architecture, algorithms, guides)
- Use the evolving/stable type lists, freshness field, and threshold defined in the project's
  CLAUDE.md schema; use `updated` and 90 days where the schema adopts that default. If type
  classification is absent or unclear, report the missing convention rather than silently classify
  pages.
- Flag evolving pages whose valid freshness date exceeds that threshold. Missing, invalid, or future
  dates are unresolved freshness metadata, never evidence of a fresh page.
- A git fallback is allowed only if the project's schema permits it and inspection of the relevant
  history/diff demonstrates a substantive content change. A last commit, file mtime, lint read, or
  mechanical metadata/link edit alone is not that evidence. Identify the fallback date and
  supporting commit explicitly; if evidence is unavailable or inconclusive, report unresolved
  freshness instead of inventing a date.
- Report: filepath, type, freshness field/date, evidence source, days since update, and applicable
  threshold. Lint reads and mechanical fixes must preserve the freshness clock.

**4. Structural Health**

Missing `## Related` sections:

- Files that contain links to other vault files but lack a `## Related` heading
- Report: filepath, count of outbound links without Related section

Frontmatter gaps:

- Read required fields, field types, and allowed values for each page type from the project's
  CLAUDE.md schema. Do not impose `title`, `status`, or `created` on a schema that does not
  require them, or rename custom fields silently.
- Report missing/invalid fields against that contract; if a page type or its contract is unknown,
  report that uncertainty rather than invent defaults.

Empty directories:

- Subdirectories with 0-1 .md files — candidates for merging

Duplicate and near-duplicate pages:

- Compare titles (fall back to note names where the schema has no title field), schema-supported
  aliases, and names normalized for case, spacing, and punctuation. Flag exact collisions and likely
  near matches, such as `OpenAI` / `Open AI`; also look for substantial content overlap among
  related candidates. Preserve meaningful qualifiers such as a person's dates or an organization's
  location when deciding identity.
- Report candidate paths, matching names/aliases or overlapping passages, and the reason for review.
  Read candidate content and source evidence to distinguish the same entity/topic from namesakes,
  intentional summaries, and separate scopes. Similarity alone is not proof of duplication.
- Recommend the Merge procedure only after human review establishes duplication; never auto-merge or
  delete candidates in lint, including `--fix`.

Frontmatter drift:

- Properties that diverge across pages (e.g. `rating` vs `score`, `date` vs `research_date`). If property names diverge, formalize a property schema listing every property name, type, and allowed values.

**5. Knowledge Gaps**

Frequently mentioned concepts without own page:

- Terms that appear in 3+ files but don't have a dedicated page
- Report: term, mention count, files mentioning it

Unprocessed sources:

- Use the same recursive enumeration and Source ingest state rules as Ingest Phase 1. Count pending raw sources (missing state or boolean `false`), including PDFs, data files, and transcripts, once each; do not count sidecars as sources.
- Report state errors separately, with source/metadata paths and reasons, and report orphan metadata. These sources are not processed; neither a state error nor a failed scan may be presented as a verified zero backlog. Lint, including `--fix`, must not set any source's state to processed.

Contradictions:

- Report conflicting claims with their source citations for human review. Recency alone does not
  establish which claim is correct; preserve the evidence for each.

Data gaps:

- Areas where the wiki is thin. Which sections have few sources? Which entities have only one mention?

#### Report

Write report to `{vault}/audits/wiki-lint-{YYYY-MM-DD}.md` using the project's separate
generated-audit-report frontmatter contract; declare that contract if absent rather than applying
wiki-note defaults. Include a summary table of issue counts per category, followed by
High/Medium/Low priority sections, and identify any incomplete scan or unresolved checks.

#### Auto-Fix (`--fix` mode)

When invoked with `--fix`, automatically:

1. **Add missing `## Related` sections**: Append to files that have outbound links but no Related section. Populate with the files they already link to.
2. **Add missing frontmatter fields**: Use only explicit per-type schema defaults that are supported
   by the note's content; leave unknown type, section, date, or other values unresolved instead of
   guessing. Never add optional `title`, `status`, or `created` fields merely because this skill
   mentions them.
3. **Preserve freshness dates**: Never reset existing `updated` or other schema-defined freshness
   dates because a page was read or mechanically edited. Do not mark a stale page fresh. Missing
   date fields can be recovered only under the schema's explicit history fallback and with the
   substantive evidence required by Content Freshness; otherwise leave them unresolved.

Do NOT auto-fix: broken or ambiguous links, orphan pages, duplicate/near-duplicate pages,
contradictions, or knowledge gaps (need human judgment). Do not create stub pages during `--fix`;
keep page creation as a concrete suggestion for review.

#### Generative suggestions

**Lint is generative, not just diagnostic.** Beyond reporting issues, suggest:

- New sources to find ("No sources cover X from the Y perspective")
- New questions to investigate ("Pages A and B imply Z, but this hasn't been verified")
- New pages to create ("Topic X is discussed in 5 places but has no dedicated page"): list the
  mentioning paths and proposed scope, check titles/aliases for existing candidates first, and
  suggest at least one referring page or INDEX entry that would link to the new page. If the user
  requests creation, add that incoming link and verify it so the suggestion does not produce an
  orphan.
- Structural improvements ("Section Y has grown to 30 pages — consider splitting")

This is more useful than just "page Z has no links."

#### Tools

- **Graph view** — shows wiki shape: hubs (highly connected), orphans (disconnected), clusters (related groups). The ideal graph has no isolated nodes and clear topical clusters.
- **Dataview queries** — dynamic audit tables over frontmatter. See `references/obsidian-tips.md` for examples.

#### Post-Lint

1. Append to log.md using the log contract:
   `## [YYYY-MM-DD] lint | Wiki health audit`, followed by a one-line summary and
   links to the report and any affected pages.
2. Inspect the complete diff of the report, log, and fixes. Verify that raw sources
   and source-state sidecars are unchanged and that lint did not reset content
   dates. Stage only this operation's paths and commit per project conventions;
   leave unrelated changes untouched. A report-only run may commit its report and
   log without changing any wiki page.
3. Report summary to user with action items

---

## Why This Works

The tedious part of maintaining a knowledge base isn't reading or thinking — it's the bookkeeping. Updating cross-references when new information arrives. Keeping summaries current. Noting when new data contradicts old claims. Maintaining consistency across dozens of pages. Humans abandon wikis because the maintenance burden grows faster than the value.

LLMs don't get bored. They don't forget to update a cross-reference. They can touch many files in one pass. The wiki stays maintained because the cost of maintenance is near zero.

The human's job: curate sources, direct the analysis, ask good questions, think about what it all means. The LLM's job: everything else.

The idea echoes Vannevar Bush's Memex (1945) — a personal, curated knowledge store with associative trails between documents. Bush's vision was closer to this than to what the web became: private, actively curated, with the connections between documents as valuable as the documents themselves. The part he couldn't solve was who does the maintenance.

The wiki is just a git repo of markdown files. You get version history, branching, and collaboration for free. Obsidian gives you the reading/browsing experience. The LLM gives you the writing and maintenance. Together they form a knowledge system that actually compounds over time.
