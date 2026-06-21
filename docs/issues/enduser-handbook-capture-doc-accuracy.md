# Capture-doc accuracy trims (forkability wording + GraphQL-only read-hatch)

**Status:** deferred (out of scope for 1.1.0) · **Section:** enduser-handbook · **Surfaced:** 2026-06-21 (PR #13)

Two small honesty/accuracy trims to the **capture** docs, surfaced by the 1.1.0 5-lens audit and deliberately deferred rather than bundled into the `static_md` adapter PR (#13) — they touch a different surface (capture, not publish) and bundling them would have widened that PR's reviewed scope. No behavior change; doc-only.

## Trim 1 — "fork it for other engines" overstates forkability

The reference enumeration/capture assets are pure Playwright TypeScript (`assets/surface-audit.playwright.ts`, `assets/capture-helpers.playwright.ts` — the latter ~645 lines). Several docs tell a non-Playwright team to "**fork it for other engines**":

- `SKILL.md:88` ("so fork the asset for other engines")
- `references/completeness-gate.md:171`
- `references/running-ui-source.md:27`
- `references/container-isolation.md:157`
- `references/manifest-discipline.md:130`
- `references/capture-spec-helpers.md:11`

The *methodology* is genuinely engine-agnostic and normative; the *asset* is not. A Cypress or Puppeteer team does not "fork" a Playwright spec — they reimplement it against a different driver API (different selector engine, different network-interception hooks, different lifecycle). "Fork it" undersells that as a copy-and-tweak. The portable part is `assets/lib/*.mjs` (engine-neutral helpers — the classifier, the inventory, the matchers), which a non-Playwright spec can import directly; the Playwright `.ts` driver glue is what gets rewritten.

**Work:** reword the "fork it for other engines" phrasing (sweep all 6 sites — keep them consistent) to something like "the methodology is normative and engine-agnostic; reimplement the Playwright `.ts` driver glue for another engine, reusing the engine-neutral `lib/*.mjs` helpers as-is." Pairs with [the capture-engines reference-doc item](enduser-handbook-capture-engines.md).

## Trim 2 — shipped read-hatch is GraphQL-only; REST/Django POST-reads fail closed

The capture guard fails closed on non-GET requests, with an opt-in read escape hatch so a POST-based read still gets captured. The shipped default classifier is **GraphQL-specific**: `classifyGraphqlRead` (`assets/lib/graphql-read-classifier.mjs`, wired in `assets/capture.example.spec.ts:59-65`, documented in `references/capture-spec-helpers.md:20,35`) only admits a POST that is an inline single-operation GraphQL **query** — everything else returns `undefined` (fail closed).

That is correct and safe, but a team whose app does **REST/RPC POST-based reads** (e.g. a Django/DRF app that reads via POST, or a JSON-RPC backend) gets every such read blocked by the default, with no doc note telling them why or what to do. The docs present the read-hatch as the general POST-read solution without flagging that the *shipped* classifier only understands GraphQL.

**Work:** add a one-paragraph note where the read-hatch is introduced (`references/capture-spec-helpers.md`, near the `classifyRequest`/`classifyGraphqlRead` description) stating that the shipped read classifier is GraphQL-only and a non-GraphQL POST-read app must supply its own `classifyRequest` that recognizes its read shape (returning `'read'` only for unambiguous, side-effect-free reads, `undefined` otherwise — same fail-closed contract). No code change required; the hook already accepts a custom `classifyRequest`.

## Notes

- Both are doc-accuracy items, not bugs — the shipped behavior is correct; the docs slightly over-claim transferability. Low priority.
- Verified against the live tree at `origin/main` 6863c29 (1.1.0). Re-grep the cited line numbers before editing — surrounding docs drift.
