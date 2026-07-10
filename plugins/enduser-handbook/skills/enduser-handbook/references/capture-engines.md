# Capture engines — per-engine recipes

The profile's `capture.engine` enum has four values: `playwright`, `cypress`, `puppeteer`,
`manual`. This file gives each one an idiomatic `capture.page_identity_signal` recipe, a
representative containerized `capture.command`, and its primitive for the fail-closed
request guard.

**Status: these are illustrative recipes, not tested contracts.** Only the Playwright case
is exercised by an in-scope project (`assets/capture-helpers.playwright.ts`,
`assets/surface-audit.playwright.ts`, `assets/capture.example.spec.ts`, all run under
`node --test` / `npx playwright test` in this repo's own tooling — no such harness exists
here for Cypress or Puppeteer). Every Cypress and Puppeteer API cited below is verified
against current vendor documentation (see [Sources](#sources)) but not run in this repo.
Do not extend these recipes from memory; re-verify against vendor docs before you rely on
an API this file does not already cite.

## The contract is engine-agnostic; the driver glue is not

[capture-spec-helpers.md](capture-spec-helpers.md), [page-identity.md](page-identity.md),
[capture-safety.md](capture-safety.md), and [container-isolation.md](container-isolation.md)
are the normative, engine-agnostic contract. Four pure modules encode the parts of that
contract that are logic rather than prose, and are **reused as-is** regardless of engine:
`capture-guard-policy.mjs` (whose exported function `decideRoute` is the ordered
fail-closed classifier), `control-inventory.mjs`, `graphql-read-classifier.mjs`, and
`identity-match.mjs`. Only the glue that feeds those modules request/response objects from
the browser is per-engine — the shipped `*.playwright.ts` files are one such glue
implementation; a Cypress or Puppeteer project reimplements the glue, not the policy.

**Load-bearing caveat: the glue carries a safety contract it must not drop.**
`decideRoute`'s `[guard:eventsource]` and `[guard:beacon]` branches key on the exact
`resourceType` tokens `'eventsource'` and `'ping'` (`assets/lib/capture-guard-policy.mjs:217,224`).
The reused classifier only preserves the SSE/beacon block if the per-engine glue populates
`GuardRequest.resourceType` with that identical vocabulary. An SSE `GET` arriving with
`resourceType: 'fetch'` or `undefined` falls through to `[guard:get-head]` and connects to
the live endpoint — a silent fail-open, not a loud one. So every section below states
whether its engine can meet that obligation, and routes the gap to the contract's existing
fail-at-install escape (`capture-spec-helpers.md`: "an engine that cannot block a socket
(only observe it) must fail at install time, not silently open") where it cannot.

## What each engine expresses

| Contract element                          | Playwright                     | Cypress                                  | Puppeteer                              |
|--------------------------------------------|---------------------------------|-------------------------------------------|------------------------------------------|
| Fail-closed request guard                 | context-level route interception | total `cy.intercept('**', …)` + `req.destroy()` | `page.setRequestInterception(true)` + `request.abort()` |
| Page-identity wait (client-rendered)      | `page.waitForResponse`         | `cy.intercept().as()` + `cy.wait('@alias')` | `page.waitForResponse`                 |
| Page-identity wait (server-rendered)      | heading/DOM assertion only     | heading/DOM assertion only               | heading/DOM assertion only             |
| `resourceType` → `decideRoute` token source | `request.resourceType()`     | `req.resourceType` — **deprecated 14.0.0**, unreliable | `request.resourceType()` — verbatim match |
| Safe modal dismiss                        | Escape, then negative control  | Escape, then negative control            | Escape, then negative control          |

## Playwright (shipped reference case)

**Identity:** the profile example ships
`page_identity_signal: "wait for response matching stack.backend.api_url_prefix, then
assert the primary page heading is visible"` (client-rendered case); a server-rendered
page drops the response wait and asserts the heading directly, per
[page-identity.md](page-identity.md). **Command:** the profile example's containerized
`capture.command` runs `npx playwright test tests/handbook --reporter=line` inside
`docker compose run` — see `assets/handbook.profile.example.yml`.

**Guard mapping:** the guard installs at **browser-context level**, before any page is
created, so a popup or new tab opened during a step is covered automatically — Playwright
is the one engine here where this is not a structural concern. `request.resourceType()`
already yields the guard's token vocabulary (`'eventsource'`, `'ping'`, …), so the shipped
`capture-helpers.playwright.ts` is the **non-normative reference implementation** of the
normative contract in `capture-spec-helpers.md`; reimplement the driver glue for another
engine, reusing the engine-neutral `assets/lib/*.mjs` helpers as-is.

## Cypress

**Identity:** the DOM heading assertion is the primary check and is sufficient on its own
(`page-identity.md`); a network wait is only a sequencing aid, never a substitute. For a
client-rendered page: alias the XHR to `stack.backend.api_url_prefix` with
`cy.intercept('**/api/whatever*').as('ready')`, `cy.wait('@ready')`, **then assert the
primary heading is visible**. For a server-rendered page: drop the intercept/wait and
assert the heading directly. `cy.wait('@alias')` resolves once the request/response cycle
completes (`wait.mdx`, see [Sources](#sources)).
Representative container command: `cypress run --browser chrome` inside the project's
container harness (see [container-isolation.md](container-isolation.md) "Common command
patterns").

**Guard mapping (fail-closed shape, explicit):** install a **total** interceptor —
`cy.intercept('**', (req) => { const decision = decideRoute(toGuardRequest(req), opts); if
(decision.action === 'block') req.destroy(); })` — that runs **every** request through
`decideRoute`, not a deny-only pattern. A denylist that only matches known-bad URLs
inverts the fail-closed contract: Cypress passes any request the interceptor does not
explicitly claim straight through, so an un-matched request is admitted by default instead
of blocked by default. `req.destroy()` fails the request with a network error
(`intercept.mdx`, see [Sources](#sources)), the same doc that documents
`{ forceNetworkError: true }`.

**Gaps — each routes to the contract's fail-at-install escape, not a silent hole:**

1. **WebSocket.** Cypress transparently proxies WebSocket connections and `cy.intercept()`
   can observe the upgrade request, but stubbing or mocking individual WebSocket frames is
   not supported. An engine that cannot block a socket, only observe it, must fail at
   install time per `capture-spec-helpers.md`, not silently allow it through.
2. **Cache.** `cy.intercept()` intercepts at the network layer; a request served from the
   browser cache never reaches the network layer and `cy.intercept()` does not fire for
   it. Disable the browser cache (and any service worker) for the capture run, or the guard
   has a blind spot it cannot detect.
3. **SSE / `resourceType` is unreliable.** `resourceType` on `cy.intercept`'s
   `RouteMatcher` and on `req` was **deprecated starting in Cypress 14.0.0**. The glue
   therefore cannot reliably feed `decideRoute` the `'eventsource'`/`'ping'` tokens the
   `[guard:eventsource]`/`[guard:beacon]` branches key on — a long-lived `EventSource` GET
   could fall through to `[guard:get-head]` and connect. Do not rely on `resourceType` for
   SSE/beacon classification on Cypress: catch those endpoints with a URL-pattern
   `denyPatterns` entry or a project-supplied `classifyRequest`, or fail at install if
   neither is feasible.

## Puppeteer

**Identity:** the DOM heading assertion is primary; for a client-rendered page,
`await page.waitForResponse(res => res.url().includes(apiUrlPrefix) && res.ok())` is the
sequencing aid before asserting the heading, mirroring the Playwright/profile phrasing
("… then assert the primary page heading is visible"). A server-rendered page drops the
wait. Representative container command: `docker run -i --init --rm --cap-add=SYS_ADMIN …`
(or `--no-sandbox` in place of the capability grant) running the project's Puppeteer
script — see `docker/README.md`. **Note:** `capture-manifest.example.yml` names the
unmaintained Python `pyppeteer` port as one possible manifest-format example; the APIs
cited in this section target the actively maintained Node `puppeteer/puppeteer` package,
whose surface `pyppeteer` mirrors closely but does not track 1:1.

**Guard mapping:** `await page.setRequestInterception(true)` then, in the `request`
handler, build a `GuardRequest` from the intercepted request and call `request.abort()` on
a `block` decision, `request.continue()` on `allow`. Pass `request.resourceType()`
**verbatim** as `GuardRequest.resourceType` — Puppeteer's `ResourceType` is
`Lowercase<Protocol.Network.ResourceType>`, so it already yields `'eventsource'`, `'ping'`,
and the rest of `decideRoute`'s vocabulary with no per-engine normalization step. This is
the one engine here that can meet the `resourceType` obligation as cleanly as Playwright.

**Structural note — interception is per-page, not per-context.** Unlike Playwright's
context-level install, Puppeteer's `setRequestInterception` is scoped to the `Page` it is
called on. A step that opens a popup or new tab yields a second, unguarded page unless the
glue also guards it. Listen for `BrowserEvent.TargetCreated`
(`browser.on('targetcreated', …)`, emitted "when a new page is opened by window.open or by
browser.newPage") and enable interception on the new page **before it navigates**; do not
assume the context-level guarantee Playwright gives you for free.

**Gaps — each routes to the contract's fail-at-install escape:**

1. **WebSocket.** Puppeteer's Fetch-domain request interception does not cover WebSocket
   connections. There is no verified block primitive for it here; fail at install rather
   than assume coverage.
2. **Cache.** Once request interception is enabled, "every request will stall unless it's
   continued, responded or aborted; **or completed using the browser cache**" — a
   cache-served request bypasses the interception handler entirely. Disable the cache (and
   any service worker) for the capture run.

## Manual (halt boundary, not a ready mode)

`manual` is not a fifth automated engine — it is a declared, honestly-documented **halt
boundary**. The skill's capture harness runs **container-only**
([container-isolation.md](container-isolation.md)): "If `capture.command` is empty or the
project clearly runs captures on the host, you do not just run it anyway. You halt and
tell the user the guarantees above are not met" (`container-isolation.md:161-166`), and W2
states plainly "you never run the engine on the host" (`SKILL.md:94`). Because a manual
capture is by definition a human at the keyboard rather than a container-run script, the
skill's automated flow **halts** on `engine: manual` rather than proceeding — it does not
attempt to drive or guard a human-operated session.

If a project nonetheless captures screenshots by hand *outside* the skill's automated
flow, there is no `decideRoute` glue to install — **the human is the guard.** The operator
is directly bound by capture-safety R5/R6: never trigger a live, irreversible, or
PII-exposing action; capture the read-only/open state and disclose the rest in prose
([capture-safety.md](capture-safety.md)). The identity checklist mirrors
[page-identity.md](page-identity.md) by hand: confirm the route matches, the loading state
is gone, the primary heading is visible (the identity check itself), and any staged
precondition still holds before the shot. Screenshots still land at
`capture.output_dir/<slug>/NN-slug.png`, and the operator is responsible for
`capture.locale` / fresh-profile discipline that a container run would otherwise enforce
automatically.

## Sources

Cypress (`cypress-io/cypress-documentation`):

- `cy.intercept` request-object properties incl. `resourceType` —
  https://github.com/cypress-io/cypress-documentation/blob/main/docs/api/commands/intercept.mdx
- `cy.wait('@alias')` resolves the request/response cycle —
  https://github.com/cypress-io/cypress-documentation/blob/main/docs/api/commands/wait.mdx
- `resourceType` on `cy.intercept` deprecated starting in Cypress 14.0.0 —
  https://github.com/cypress-io/cypress-documentation/blob/main/docs/app/references/migration-guide.mdx
- WebSocket connections transparently proxied; frame stubbing not supported —
  https://github.com/cypress-io/cypress-documentation/blob/main/docs/app/references/trade-offs.mdx
  and https://github.com/cypress-io/cypress-documentation/blob/main/docs/app/guides/network-requests.mdx
- Cache-served requests never reach `cy.intercept()` — same `intercept.mdx` as above.
- `cypress run --browser chrome` —
  https://github.com/cypress-io/cypress-documentation/blob/main/docs/app/references/launching-browsers.mdx

Puppeteer (`puppeteer/puppeteer`):

- `ResourceType = Lowercase<Protocol.Network.ResourceType>` —
  https://github.com/puppeteer/puppeteer/blob/main/docs/api/puppeteer.resourcetype.md
- `page.setRequestInterception(true)`; "every request will stall unless it's continued,
  responded or aborted; or completed using the browser cache" —
  https://github.com/puppeteer/puppeteer/blob/main/docs/api/puppeteer.page.setrequestinterception.md
- `request.abort()` —
  https://github.com/puppeteer/puppeteer/blob/main/docs/api/puppeteer.httprequest.abort.md
- `page.waitForResponse(urlOrPredicate)` —
  https://github.com/puppeteer/puppeteer/blob/main/docs/api/puppeteer.page.waitforresponse.md
- `BrowserEvent.TargetCreated` — emitted when a new page opens via `window.open` or
  `browser.newPage` —
  https://github.com/puppeteer/puppeteer/blob/main/docs/api/puppeteer.browserevent.md
- Container invocation (`--cap-add=SYS_ADMIN` or `--no-sandbox`) —
  https://github.com/puppeteer/puppeteer/blob/main/docker/README.md
