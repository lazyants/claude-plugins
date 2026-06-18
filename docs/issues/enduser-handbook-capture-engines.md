# Capture-engine reference docs beyond Playwright

**Status:** deferred · **Section:** enduser-handbook · **Surfaced:** 2026-06-18 (PR #2)

## Problem

The capture references (`manifest-discipline.md`, `page-identity.md`, `container-isolation.md`) describe engine-agnostic principles, and `capture.command` + `capture.page_identity_signal` are the per-project escape hatch for any engine. But there is no worked reference for the non-Playwright engines the profile enumerates (`cypress`, `puppeteer`, `manual`). A project on one of those gets principles but no concrete `page_identity_signal` / command recipe to model.

## Work

Add short engine recipe docs (or a single `references/capture-engines.md`) giving, per engine, the idiomatic `page_identity_signal` string and a representative `capture.command`:
- **Cypress** — `cy.intercept` + `cy.wait('@alias')`, `cypress run` command.
- **Puppeteer** — `page.waitForResponse`, headless-chrome container command.
- **Manual** — checklist for hand-captured screenshots with the same naming/identity discipline.

## Notes

- Not blocking: the escape hatch already lets any engine work. This is documentation polish to lower the per-project authoring cost.
