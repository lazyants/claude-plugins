# Backlog

Local backlog for `lazyants/claude-plugins`. One file per item under [`docs/issues/`](issues/); this index links them by section. Not filed on any external tracker.

## enduser-handbook

Follow-ups surfaced while shipping the `enduser-handbook` plugin (PR #2, 2026-06-18).

- [Migrate vpp/plattform onto the enduser-handbook plugin](issues/vpp-migrate-to-enduser-handbook.md) — port the bespoke `vpp-handbook` skill to a profile + style guide, gated on `regression-checks.sh`.
- [Additional publish-target adapters](issues/enduser-handbook-publish-adapters.md) — Confluence / GitBook / Docusaurus / static-md adapters beyond v1's `obsidian-vault`.
- [Capture-engine reference docs beyond Playwright](issues/enduser-handbook-capture-engines.md) — Cypress / Puppeteer / manual guidance; the `capture.command` escape hatch covers them today but has no reference doc.
- [`/scaffold-profile` slash command](issues/enduser-handbook-scaffold-profile.md) — interrogate a project and stamp a starter `.claude/handbook/profile.yml`.
- [Richer `style_guide.inline` schema or a profile validator](issues/enduser-handbook-profile-validation.md) — v1 leaves value-shape validation to Claude; revisit once the schema settles.
