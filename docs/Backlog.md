# Backlog

Local backlog for `lazyants/claude-plugins`. One file per item under [`docs/issues/`](issues/); this index links them by section. Not filed on any external tracker.

## enduser-handbook

Follow-ups surfaced while shipping the `enduser-handbook` plugin (PR #2, 2026-06-18).

- [Migrate vpp/plattform onto the enduser-handbook plugin](issues/vpp-migrate-to-enduser-handbook.md) — port the bespoke `vpp-handbook` skill to a profile + style guide, gated on `regression-checks.sh`.
- [Additional publish-target adapters](issues/enduser-handbook-publish-adapters.md) — Confluence / GitBook / Docusaurus / static-md adapters beyond v1's `obsidian-vault`.
- [Capture-engine reference docs beyond Playwright](issues/enduser-handbook-capture-engines.md) — Cypress / Puppeteer / manual guidance; the `capture.command` escape hatch covers them today but has no reference doc.
- [`/scaffold-profile` slash command](issues/enduser-handbook-scaffold-profile.md) — interrogate a project and stamp a starter `.claude/handbook/profile.yml`.
- [Richer `style_guide.inline` schema or a profile validator](issues/enduser-handbook-profile-validation.md) — v1 leaves value-shape validation to Claude; revisit once the schema settles.
- [Optional structural-identity-only mode for the surface audit](issues/enduser-handbook-surface-audit-structural-mode.md) — declined for 1.0.5 in favor of the documented PII boundary; revisit if seeded-data + human-scrub proves insufficient. _(PR #9, 2026-06-20)_
- [Capture guard: recursively percent-decode for the dangerous-verb hint](issues/enduser-handbook-guard-recursive-decode.md) — low-pri defense-in-depth; a doubly-encoded destructive GET evades the verb hint (denyPatterns + fail-closed-on-non-GET remain primary). _(PR #9, 2026-06-20)_
- [Per-role re-audit of the interactive surface](issues/enduser-handbook-per-role-reaudit.md) — run the surface audit across N roles and diff the surfaces so role-gated controls aren't missed; deferred from 1.0.6. _(PR #11, 2026-06-20)_
- [State-variant capture (empty / error / permission-denied)](issues/enduser-handbook-state-variant-capture.md) — drive the app into empty/error/denied states and capture them as labelled variants; deferred from 1.0.6. _(PR #11, 2026-06-20)_
