# Publish-target adapters

This directory holds one Markdown file per supported publish target. The base `SKILL.md` selects an adapter at the publish step by reading `references/publish-targets/<resolved-name>.md`, where `<resolved-name>` is the `publish.target` value in the consuming project's `.claude/handbook/profile.yml`, lowercased with underscores replaced by hyphens (see "Naming").

## Selection mechanism

At the publish step, the base skill does this (in prose, not code):

1. Read `publish.target` from the loaded profile.
2. Try to open `references/publish-targets/<resolved-name>.md`, where `<resolved-name>` is the `publish.target` value lowercased with underscores replaced by hyphens (e.g. `obsidian-vault.md` when `publish.target: obsidian_vault`, `static-md.md` when `publish.target: static_md`). See "Naming" below.
3. If the file does not exist, halt with: `"No publish-target adapter for <value>. Available: <list of files in this directory minus README.md>."` No partial output, no file writes.
4. Otherwise follow the adapter's instructions to write the chapter, wire it into the index/parent, and add glossary entries.

The base skill never branches on target name in its own prose. All target-specific knowledge lives in the adapter file. This is the only extension point.

## What ships

Two adapters ship: `obsidian-vault.md` and `static-md.md`. The latter is the universal plain-Markdown fallback — it publishes to a GitHub wiki, an MkDocs tree, a GitBook (via `SUMMARY.md`), or a plain repo. Three candidates remain unimplemented — `confluence`, `gitbook`, `docusaurus`; setting `publish.target` to any of those triggers the halt above.

## Adding a new target X

Create `<X>.md` in this directory (filename must match the `publish.target` value, kebab-cased; e.g. `publish.target: confluence` -> `confluence.md`). Model it on `obsidian-vault.md` and document, in second-person voice, every binding the base skill needs to write a chapter through this target. Concretely, your adapter MUST cover:

- **Chapter file creation.** Where the chapter file lands relative to `publish.chapters_dir`, what extension it uses, what filename convention (slug from the manifest, kebab-case, etc.), and how to handle name collisions.
- **Index / parent wiring.** How to register the new chapter in `publish.index_file` (or the target's equivalent parent structure — a Confluence parent page id, a Docusaurus `sidebars.js` entry, a GitBook `SUMMARY.md` line). Specify exactly which file you edit and where in it.
- **Frontmatter / metadata.** Whether the target needs frontmatter at all. If `publish.frontmatter_required: true`, document the exact required keys (e.g. `title`, `language`, `audience`, tags) and how their values are derived from the profile and the manifest entry. If the target uses a different metadata mechanism (Confluence labels, Docusaurus `id`/`sidebar_position`), document that instead.
- **Glossary entries.** How to write glossary entries to `publish.glossary_dir`: one file per term vs. a single combined file, naming convention, cross-link format, and how to seed from `publish.glossary_seed`.
- **Asset layout.** Where screenshots and other assets from `capture.output_dir` are referenced from inside the chapter file. If the target requires assets to live in a target-specific location (e.g. uploaded to a Confluence attachment endpoint), document the move/copy step.
- **Link format.** Whether links between chapters use `publish.wikilinks: true` (Obsidian-style `[[Target]]`) or plain Markdown `[text](path)`. If the target supports both, say which to prefer and why. If the target ignores `publish.wikilinks`, say so explicitly.
- **Section labels.** How `publish.section_labels.prerequisites` and `publish.section_labels.related` are substituted into the chapter — usually verbatim under H2 headings, but the target may impose a different heading level or a structured field.
- **Group handling: support or halt.** 1.5.0 adds an optional two-level grouped nav — a manifest entry may carry `group` + `group_title` (see `manifest-discipline.md`); the axis is active the moment ANY entry in the manifest carries `group` (`anyGroup`). If your target can express two levels of nav, mirror the shipped adapters' group-aware chapter path and asset dir (see "Chapter path" and "Assets" in `static-md.md`, or the equivalent in `obsidian-vault.md`) and their index-wiring machinery — container find/create/append, the step-0 idempotency check that makes re-runs converge, and the wrong-container / duplicate-line / manual-wiring halts — gating that index-wiring machinery on `anyGroup(entries)` so a group-free manifest skips it. **Not every branch should be gated this way.** Three exceptions are in `assets/lib/chapter-paths.mjs` — `staticEmbedPath` (the asset-embed path formula, always the full-target `relative()` join, never a legacy concatenation), `validateGroups` (the duplicate-slug halt — globally unique across all groups by default, or unique within each group when the consuming profile sets `publish.per_group_slug_uniqueness: true`, which re-admits cross-group basename collisions for bare-`[[slug]]` / Quartz-shortest consumers — see `manifest-discipline.md`), and `currentIndexExpectedTarget`'s wikilinks-mode index-target formula (vault-root-relative since 1.8.0, regardless of grouping — see `obsidian-vault.md`'s "Wikilinks vs Markdown links") — model your target's embed formula, duplicate-slug check, and (if your target has an equivalent wikilink-style bare-name resolution) index-target formula on those, ungated. Your own adapter may ALSO have group-free behavior changes beyond those three module-level exceptions — for example, in `publish.wikilinks: false` mode `obsidian-vault.md` applies the full-target glossary formula and runs the Markdown-link integrity gate for group-free manifests too — so do not assume `anyGroup` gating covers every branch; state explicitly, per section, which behaviors apply to a group-free manifest. If your target cannot express two levels of nav, it MUST halt the moment it sees any entry carrying `group` — never silently flatten a grouped manifest into a flat layout, and never drop `group_title`. State explicitly in your adapter file which case applies. Nested groups (`a/b`) are out of scope for every adapter in 1.5.0.
- **Halt conditions.** Any precondition the adapter must check before writing (e.g. the index file exists, the chapters_dir is writable, the target's required tooling is reachable). List the exact halt messages.

Keep the adapter file to roughly the same shape and length as `obsidian-vault.md`. Reference profile keys exactly as written in the profile schema (e.g. `publish.chapters_dir`, not "the chapters directory"). Do not duplicate generic discipline (anti-fabrication, completeness, glossary policy) — that lives in the sibling `references/` files and applies regardless of target.

## Naming

The filename is the contract. `publish.target: obsidian_vault` reads `obsidian-vault.md` (underscores in the profile value become hyphens in the filename, lowercase throughout). Keep names short, descriptive, and free of vendor versioning — `confluence.md`, not `confluence-cloud-2026.md`. If a target genuinely needs two variants (e.g. Confluence Cloud vs. Server with different APIs), use `confluence-cloud.md` and `confluence-server.md` and let the consumer pick via `publish.target`.
