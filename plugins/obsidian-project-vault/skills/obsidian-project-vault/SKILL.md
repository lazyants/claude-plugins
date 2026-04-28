---
name: obsidian-project-vault
description: Set up, migrate, or operate an Obsidian vault as an LLM Wiki — a persistent, compounding knowledge base maintained by Claude Code. Three-layer architecture (raw sources, wiki, schema), setup modes (create, migrate, audit), ongoing operations (ingest, query, lint). Covers vault/ subfolder setup, .gitignore for .obsidian/, vault MCP config, INDEX.md navigation, Report template with frontmatter, CLAUDE.md workflow integration, data-dump handling, migration from standalone vault with diff-before-delete safety, structural audit, source ingestion workflows, query-and-file-back, and ongoing maintenance. Triggers on "set up obsidian", "create vault", "migrate vault", "audit vault", "obsidian documentation", "project knowledge base", "obsidian for project", "LLM wiki", "wiki-lint", "lint wiki", "check vault health", "audit docs", "wiki health", "ingest", "process sources", "wiki-ingest", "update wiki from sources".
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
{"command": "npx", "args": ["-y", "@bitbonsai/mcpvault@latest", "/absolute/path/to/vault"]}
```

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
- Ingest conventions (what page types to create, how to cross-reference)
- When to file query answers back vs leave in chat

### Note template

`vault/templates/<NoteType>.md`:
```yaml
---
type: report
section:
date:
tags: []
---
```

Adapt fields to domain. Keep minimal — Claude adds fields as needed.

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

If project has files overlapping with vault, diff BEFORE deleting. Produce 3 lists:

| List | Action |
|------|--------|
| In BOTH | Vault copy authoritative if enriched. Delete project copy. |
| ONLY in project | Copy to vault first. |
| ONLY in vault | Safe, no action. |

Never delete without this verification. A missed file is data loss.

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

Sample 10 files across sections. Minimum: `type`, `section`, `date`. Flag gaps. If property names diverge across notes (e.g. `rating` vs `score`), formalize a property schema document listing every property name, type, and allowed values.

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

Search the raw sources directory for files with `ingested: false` in frontmatter. If no unprocessed sources found, report and exit.

#### Phase 2: Read and discuss (interactive mode)

For each unprocessed source:

1. **Read** the full source file
2. **Summarize** the key takeaways in 3-5 bullet points
3. **Ask the user**:
   - What aspects are most important?
   - Should this create a new wiki page or update existing ones?
   - What directory should new pages go in? (suggest based on content)
4. **Identify** which existing wiki pages this source relates to:
   - Search INDEX.md for related topics
   - Grep for mentions of key concepts across the vault
   - List the pages that should be updated

In `--batch` mode: skip the discussion, auto-determine target pages and emphasis.

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

**D. Update source frontmatter:**

```yaml
ingested: true
ingested_date: YYYY-MM-DD
```

**E. Update frontmatter on modified wiki pages:**

- Set `updated: YYYY-MM-DD` on every page that was edited

#### Phase 4: Log and report

1. Append to log.md: `### YYYY-MM-DD | ingest | {source title}`
2. Update INDEX.md if new sections or directories were created
3. Report to user: sources processed, pages created, pages updated, key insights

#### Phase 5: Commit

```bash
git add {vault}/
git commit -m "Ingest: {source title or 'N sources'}"
```

Push per project conventions — do not auto-push unless CLAUDE.md specifies it.

#### Quality checks

Before committing, verify:

- [ ] All new pages have `## Related` with 2+ links
- [ ] All updated pages have `updated:` date refreshed
- [ ] Source file marked as `ingested: true`
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

Update INDEX.md and log.md when filing a query result back.

### Lint

Periodic content health-check. Distinct from Audit (one-time structural check) — Lint is ongoing maintenance that keeps the wiki accurate and alive as it grows.

Arguments:

- No args: full audit (all checks)
- `--quick`: only broken links and orphans (fast)
- `--fix`: auto-fix all fixable issues without prompting

#### Checks

Run ALL checks below. Use Grep and Glob tools for efficient scanning — do NOT read every file in full.

**1. Link Integrity**

Broken internal links:

- Grep for `]\(` patterns across all `.md` files in the vault
- For each relative link, resolve the path and verify the target file exists
- Report: file, line number, broken link target
- GOTCHA: Obsidian uses shortest-path resolution by default — `[[subfolder/Name]]` resolves to any `Name.md` in the vault, not just `subfolder/Name.md`. A grep-based check that treats the full path as a filename will produce false positives for path-prefixed links. Verify in Obsidian or check for the basename only.

External URL candidates:

- Grep for `](http` patterns
- Flag URLs older than 6 months (based on file's `created` date)
- Report as "may need verification" — do NOT fetch URLs

**2. Page Connectivity**

Orphan pages (no inbound links):

- Build a set of all `.md` files (excluding INDEX.md, Dashboard.md, README.md, log.md, templates, attachments, raw sources)
- For each file, grep the rest of the vault for links to it
- Pages with zero inbound links are orphans

Missing backlinks (A → B but B ↛ A):

- For each file with a `## Related` section, extract its outbound links
- Check if each linked target has a link back

Hub pages (10+ inbound links):

- Report as informational, not an issue

**3. Content Freshness**

Stale pages:

- Only check evolving page types (e.g., research, ideas, decisions, experiments) — skip stable types (e.g., architecture, algorithms, guides)
- Which types are evolving vs stable is defined in the vault's CLAUDE.md schema
- Flag pages where `updated` date is > 90 days ago
- Report: filepath, type, updated date, days since update

**4. Structural Health**

Missing `## Related` sections:

- Files that contain links to other vault files but lack a `## Related` heading
- Report: filepath, count of outbound links without Related section

Frontmatter gaps:

- Required fields: title, type, status, created, updated
- Report files with missing fields

Empty directories:

- Subdirectories with 0-1 .md files — candidates for merging

Duplicate titles:

- Extract `title:` from all frontmatter, flag duplicates

Frontmatter drift:

- Properties that diverge across pages (e.g. `rating` vs `score`, `date` vs `research_date`). If property names diverge, formalize a property schema listing every property name, type, and allowed values.

**5. Knowledge Gaps**

Frequently mentioned concepts without own page:

- Terms that appear in 3+ files but don't have a dedicated page
- Report: term, mention count, files mentioning it

Unprocessed sources:

- Count files in raw sources directory with `ingested: false`

Contradictions:

- Pages that make claims conflicting with each other. Newer sources should generally supersede older claims, but flag for human review.

Data gaps:

- Areas where the wiki is thin. Which sections have few sources? Which entities have only one mention?

#### Report

Write report to `{vault}/audits/wiki-lint-{YYYY-MM-DD}.md` with frontmatter (`type: audit`, `status: current`) and a summary table of issue counts per category, followed by High/Medium/Low priority sections.

#### Auto-Fix (`--fix` mode)

When invoked with `--fix`, automatically:

1. **Add missing `## Related` sections**: Append to files that have outbound links but no Related section. Populate with the files they already link to.
2. **Add missing frontmatter fields**: Fill in defaults — `status: current`, `created:` and `updated:` from git history.
3. **Update stale `updated:` dates**: For pages reviewed during this lint pass, update to today.
4. **Create stub pages for knowledge gaps**: For concepts mentioned in 5+ files, create a stub page with `status: draft` and links to the mentioning files.

Do NOT auto-fix: broken links, orphan pages, duplicate titles (need human judgment).

#### Generative suggestions

**Lint is generative, not just diagnostic.** Beyond reporting issues, suggest:

- New sources to find ("No sources cover X from the Y perspective")
- New questions to investigate ("Pages A and B imply Z, but this hasn't been verified")
- New pages to create ("Topic X is discussed in 5 places but has no dedicated page")
- Structural improvements ("Section Y has grown to 30 pages — consider splitting")

This is more useful than just "page Z has no links."

#### Tools

- **Graph view** — shows wiki shape: hubs (highly connected), orphans (disconnected), clusters (related groups). The ideal graph has no isolated nodes and clear topical clusters.
- **Dataview queries** — dynamic audit tables over frontmatter. See `references/obsidian-tips.md` for examples.

#### Post-Lint

1. Append to log.md: `### YYYY-MM-DD | lint | Wiki health audit`
2. Commit changes
3. Report summary to user with action items

---

## Why This Works

The tedious part of maintaining a knowledge base isn't reading or thinking — it's the bookkeeping. Updating cross-references when new information arrives. Keeping summaries current. Noting when new data contradicts old claims. Maintaining consistency across dozens of pages. Humans abandon wikis because the maintenance burden grows faster than the value.

LLMs don't get bored. They don't forget to update a cross-reference. They can touch many files in one pass. The wiki stays maintained because the cost of maintenance is near zero.

The human's job: curate sources, direct the analysis, ask good questions, think about what it all means. The LLM's job: everything else.

The idea echoes Vannevar Bush's Memex (1945) — a personal, curated knowledge store with associative trails between documents. Bush's vision was closer to this than to what the web became: private, actively curated, with the connections between documents as valuable as the documents themselves. The part he couldn't solve was who does the maintenance.

The wiki is just a git repo of markdown files. You get version history, branching, and collaboration for free. Obsidian gives you the reading/browsing experience. The LLM gives you the writing and maintenance. Together they form a knowledge system that actually compounds over time.
