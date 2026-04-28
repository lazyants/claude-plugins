# Obsidian Workflow Tips for LLM Wiki

Tips for the human side of the LLM Wiki workflow. Surface these when setting up
a vault or when the user asks about Obsidian workflow optimization.

## Source Ingestion

- **Obsidian Web Clipper** — browser extension that converts web articles to clean
  markdown. Fastest way to populate raw sources from the web.
- **Image handling** — In Obsidian Settings → Files and links, set "Attachment folder
  path" to a fixed directory (e.g. `raw/assets/`). In Settings → Hotkeys, bind
  "Download attachments for current file" to a shortcut (e.g. Ctrl+Shift+D). After
  clipping an article, hit the hotkey to download all images locally. This prevents
  broken image links when URLs expire.
- **LLMs and images** — LLMs can't natively read markdown with inline images in one
  pass. Workaround: read the text first, then view referenced images separately for
  additional context.

## Navigation & Visualization

- **Graph view** — best way to see the shape of the wiki: hubs (highly connected pages),
  orphans (disconnected), clusters (related pages). Open via sidebar or Ctrl+G.
  The ideal graph has no isolated nodes and clear topical clusters.
- **Quick Switcher** (Ctrl+O) — fastest navigation in large vaults. Works best with
  unique filenames across the vault.
- **Backlinks panel** — shows which pages link to the current page. Useful for
  understanding how a concept connects to the rest of the wiki.

## Dynamic Queries (Dataview Plugin)

Dataview runs queries over page frontmatter. Useful Dashboard widgets:

```dataview
LIST FROM "<knowledge>" WHERE length(file.inlinks) > 0 SORT file.mtime DESC LIMIT 10
```
(recently modified pages with inlinks — active hub pages)

```dataview
TABLE WITHOUT ID file.link, type, date FROM "<knowledge>" WHERE !tags
```
(pages missing tags — candidates for lint)

```dataview
LIST FROM "<knowledge>" WHERE type = "source-summary" AND length(file.outlinks) < 3
```
(source summaries with few outgoing links — probably need cross-referencing)

**Note:** Dataview queries only render in Obsidian UI, not in plain markdown.
Keep Dashboard.md readable without Dataview by including static content alongside queries.

## Presentations

- **Marp plugin** — markdown-based slide deck format. Generate presentations directly
  from wiki content. Useful for sharing wiki knowledge in meeting formats.

## General

- The wiki is a git repo of markdown files. You get version history, branching,
  diffing, and collaboration for free.
- No Obsidian Sync if using git — two sync mechanisms fight.
- Obsidian Bases (`.base` files) provide structured database views over vault content.
  If installed (via kepano/obsidian-skills), can replace manual INDEX.md with dynamic
  filtered views.
