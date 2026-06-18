# Additional publish-target adapters for enduser-handbook

**Status:** deferred · **Section:** enduser-handbook · **Surfaced:** 2026-06-18 (PR #2)

## Problem

v1 ships only the `obsidian-vault` publish-target adapter. The profile schema already enumerates `confluence`, `gitbook`, `docusaurus`, `static_md` as `publish.target` values, and the base skill halts cleanly when an unimplemented target is selected. No adapter exists for them yet.

## Work (per target, when a real project needs it)

Add `references/publish-targets/<target>.md` following the `obsidian-vault.md` shape and the contract in `references/publish-targets/README.md`. Each adapter must document: chapter file creation relative to `publish.chapters_dir`, index/parent wiring, frontmatter/metadata handling, glossary entry layout, asset layout from `capture.output_dir`, link format (honoring or ignoring `publish.wikilinks`), section-label substitution, and halt conditions.

## Notes

- Explicitly demand-driven — do not build speculative adapters with no consumer (plan non-goal). File the concrete target here when a project picks it.
- Adding a target is additive: no change to the base skill, which resolves the adapter by filename.
