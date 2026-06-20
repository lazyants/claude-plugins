# Optional structural-identity-only mode for the surface audit

**Status:** deferred (declined for 1.0.5) · **Section:** enduser-handbook · **Surfaced:** 2026-06-20 (PR #9)

## Problem

The `surface-audit` mechanical pass (`assets/surface-audit.playwright.ts` + `assets/lib/control-inventory.mjs`) over-captures the interactive surface on purpose ("never filter; the human classify pass decides"). v1.0.5 hardened it so it suppresses the deterministic PII paths — value-bearing user data, and the aggregate text of non-control regions / containers (a `<div data-testid>` data region, a row that wraps a child control). What it still logs verbatim is a **genuine leaf control's own identity label** (`text` / `aria-label` / `title` / `href`).

That residual is irreducible by structure: an icon-only control (`<span aria-label="Delete">`) is indistinguishable from a labelled data region (`<div aria-label="Jane jane@example.com">`), so `aria-label`/`title` cannot be stripped without dropping real icon-only controls — the exact "icon-only control dropped" bug the plugin exists to prevent. So a control whose own label happens to contain PII (a clickable customer name) is logged.

For 1.0.5 this was resolved as a **documented boundary**: run the audit against seeded / non-PII data (`capture-safety.md` seed hermeticity) and scrub residual PII from the matrix labels in the human classify pass before committing (`completeness-gate.md`). The rationale for not building a redactor: a pattern mask gives false assurance because a bare name defeats pattern-matching.

## Work

If the documented boundary proves insufficient in practice, add an **opt-in** structural-identity-only mode for the audit: for non-genuine-leaf elements (and optionally all elements) emit only structural identity (`tag` / `role` / `name` / `data-testid`) + a derived destructive hint, never raw `text` / `aria-label` / `title` / `href`. Accept the cost: icon-only controls lose their `aria-label` label in the matrix (must be read from the live UI), and some genuine controls' labels degrade to a test-id. Make it a profile flag so the default keeps today's richer, boundary-documented behavior.

## Notes

- Declined for 1.0.5 deliberately (user decision): the documented boundary was chosen over structural-only because a partial redactor gives false assurance. This item just keeps the alternative tracked.
- Surfaced across the v1.0.5 adversarial-review rounds; see the PII-boundary section in `completeness-gate.md` and the `PII BOUNDARY` banner in `surface-audit.playwright.ts`.
