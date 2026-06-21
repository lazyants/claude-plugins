# Additional publish-target adapters for enduser-handbook

**Status:** partially done (static_md shipped v1.1.0; Confluence/GitBook/Docusaurus deferred) · **Section:** enduser-handbook · **Surfaced:** 2026-06-18 (PR #2)

## Problem

The plugin now ships the `obsidian-vault` and `static-md` publish-target adapters. The profile schema also enumerates `confluence`, `gitbook`, `docusaurus` as `publish.target` values, and the base skill halts cleanly when an unimplemented target is selected. No adapter exists for those three yet.

## Work (per target, when a real project needs it)

Add `references/publish-targets/<target>.md` following the `obsidian-vault.md` shape and the contract in `references/publish-targets/README.md`. Each adapter must document: chapter file creation relative to `publish.chapters_dir`, index/parent wiring, frontmatter/metadata handling, glossary entry layout, asset layout from `capture.output_dir`, link format (honoring or ignoring `publish.wikilinks`), section-label substitution, and halt conditions.

## Notes

- Explicitly demand-driven — do not build speculative adapters with no consumer (plan non-goal). File the concrete target here when a project picks it.
- Adding a target is additive: no change to the base skill, which resolves the adapter by filename.
