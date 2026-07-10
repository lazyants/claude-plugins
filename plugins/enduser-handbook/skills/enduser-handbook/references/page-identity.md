# Page identity

Before each screenshot, prove the page actually rendered what you expect. One explicit visibility
assertion immediately before each capture — every time, no exceptions.

## Why this matters

A screenshot of the wrong page silently poisons the handbook. The capture run exits green, the PNG
lands on disk, the chapter embeds it, and the reader sees a loading shell, a stale route, an error
toast, or someone else's data masquerading as the documented feature. Nothing in the publish step
can detect that. The only place to catch it is at capture time.

Common failure modes you are guarding against:

- The framework returned `200 OK` but the client-side app has not yet rendered the post-mount
  payload — the screenshot captures the empty shell or a spinner.
- A redirect fired (session expired, role gate, locale negotiation) and you screenshot the login
  page or a "permission denied" view instead of the target route.
- The previous step left a modal, toast, or banner overlaying the page; the screenshot includes
  it and the chapter shows a state the reader will never reproduce.
- The route loaded but the data the chapter narrates is absent (empty list, filtered out, a role
  flag from `capture.role_flags` grants the control but the role has no rows to show).
- A protocol-level success (auth, HTTP) does not mean a content-level success. Assert content.

## The principle

For every screenshot the chapter will embed, the capture spec must, immediately before the shot:

1. Confirm the page is the one you navigated to — the route, URL, or screen identifier matches
   what the manifest entry declares.
2. Confirm the page has finished rendering — the primary heading, main container, or
   distinguishing element the chapter is about is actually present and visible. **This DOM
   assertion is the primary identity check, and it is sufficient on its own.** Asserting the
   primary heading/element directly works for every page, server-rendered or client-rendered;
   you do not need to observe network traffic to know a page rendered.
3. Confirm the loading state is gone — no spinner, skeleton, or "please wait" overlay covering
   the area the screenshot will capture.
4. Confirm the specific element the step narrates is visible — if the step says "click the
   Export button", the Export button must be asserted visible before the click and before the
   shot that documents the click.

If any of these fail, the run must **fail loudly** — never fall back to capturing whatever is on
screen. A wrong screenshot is worse than no screenshot, because the chapter will ship it.

**Assert visibility on a content-bearing element, never a bare layout wrapper.** `visible` is a
*layout* predicate: a container whose only content is floated out of normal flow (an uncleared
float, no clearfix or `overflow`), an empty auto-sized grid track or flex line, or a
`display: contents` node (which generates no box of its own) ends up with a **zero-height box —
or no box at all**, and a visibility assertion (`toBeVisible()` / `waitFor({ state: 'visible' })`)
requires a non-empty bounding box — width **and** height > 0. So it reports such a wrapper
*hidden* even though its content paints — a loud false-negative that fails the run on a page that
rendered correctly, and may tempt you to wrongly drop a real, capturable step. Target the heading,
a text-bearing element, or a leaf with intrinsic size; if you must anchor on a collapse-prone
container, assert a **child that has height**. `toBeAttached()` / `state: 'attached'` only
*supplements* this — attachment does not prove the page rendered, which is the whole point of the
check, so it is never a standalone replacement for a visibility assertion.

### Server-rendered pages and the XHR wait

For a **server-rendered page with no post-mount XHR** — the markup arrives complete in the
initial document and nothing fetches more on mount — the heading/DOM assertion in step 2 is
the whole identity check. Assert the primary heading/container directly and shoot. Do **not**
reach for a network/response wait: there is no post-mount request to wait for, so a
"wait for the API response" step would hang or time out on a page that is already fully rendered.

Waiting on a specific XHR/response is **one option, applicable only to client-rendered pages
that fetch their payload after mount** — it is not the default. For those pages, the response
wait is a useful way to know the post-mount data has landed before you assert step 2. But the
DOM assertion is still what proves identity; the XHR wait is at most a sequencing aid that
precedes it. Choose the response wait when the page is client-rendered and the chapter's
content depends on a post-mount fetch; otherwise assert the heading/DOM and move on.

If a manifest step cannot pass its identity assertion, drop it from the chapter. Do not narrate
a step you could not capture.

## How `capture.page_identity_signal` is consumed

The profile carries a free-text English description of the engine-specific signal under
`capture.page_identity_signal`. You embed that literal string verbatim into the capture-step
instruction you write for the manifest or spec — you do not paraphrase it, translate it, or
"interpret" it into your own words first. The exact wording the project author chose is the
contract.

You then translate that natural-language directive into the appropriate engine call given
`capture.engine` and `capture.command`. The profile string says *what to wait for and what to
assert*; you decide *which API of the configured engine expresses that*. The translation is
your responsibility per project; the principle is non-negotiable across all projects.

If `capture.page_identity_signal` is empty or absent, the principle still applies — pick the
strongest identity check the engine supports (a visible primary heading or a unique
content-bearing element) and document the choice in the capture spec so the next author knows
why that selector was used.

## Anti-fabrication tie-in

This rule is the load-bearing half of the anti-fabrication discipline (see
`references/anti-fabrication.md`). Every other rule about "do not invent UI" depends on the
screenshot being of the page you think it is. If page identity is not asserted, anti-fabrication
cannot hold — you would be quoting a heading that is not on the page the reader will land on.

## What to write into the spec

For each capture step, the spec should:

- Navigate to the route from the manifest entry.
- Apply the `capture.page_identity_signal` directive verbatim (translated to the engine).
- Assert the specific element the step narrates is visible.
- Take the screenshot.
- If the next step changes state on the same page (opens a modal, expands a row), repeat the
  visibility assertion for the new element before the next shot.

Never collapse "navigate → assert → shoot" into "navigate → shoot". The middle step is the whole
point.

## Screenshot guidance

Identity asserts that you are on the right page; these two rules keep the shot itself
representative of what a reader will see.

- **Reset session-persisted filters before an "overview" shot.** Many list/table screens
  persist the user's last filter, search, or sort in the session (server-side, a cookie, or
  local storage), so the page reopens already filtered. An "overview" screenshot taken in that
  state is poisoned by a stale filter and shows a narrowed list, not the overview. Before an
  overview capture, clear persisted filters — e.g. navigate with a `?clear-filter`-style
  param the app honours, or otherwise reset to the unfiltered default — so the shot shows the
  full surface the chapter claims to.
- **When a shot deliberately stages a data-state, assert fail-closed that the precondition
  held.** Some shots need the backing data shaped first — emptying a list to document the
  empty state, clearing a logo so the upload field renders instead of the image, seeding a
  fixture, assuming a reduced role. Page identity proves you are on the right *route*, but the
  route and heading are identical for the staged and the un-staged page, so a forgotten or
  **reverted** precondition (you restored the logo before re-capturing) passes identity and
  silently ships the wrong-but-real state. Pair every staged precondition with an explicit
  assertion that it holds — assert the staged-state marker is **present** and/or the wrong-state
  marker is **absent** — before the shot, so a missed precondition fails the run instead of
  shipping the wrong screen. The empty / error / permission-denied state variants are the
  canonical case this rule exists for — see `references/state-variants.md` for the full
  methodology and the `state` option on `assertIdentity`.
- **For long unpaginated lists, capture the viewport, not the full element.** A full-element
  screenshot of a list that renders all rows (no pagination) produces an unusably tall image —
  a 100-row table becomes a strip nobody can read. Capture the **viewport** (the visible
  window) for these, so the shot is a normal screen the reader recognizes. Reserve
  full-element capture for short, bounded elements.
- **For a region whose height can balloon (a tall modal or list), cap it.** Pass
  `captureRegion`'s `{ maxHeight }` to clamp the rendered height; content below the clamp is
  hidden, so paginate the capture in sections or disclose the remainder in prose. See
  `capture-spec-helpers.md`.
- **Let transitions settle before shooting.** A shot fired immediately after a modal-open or
  row-expand assertion can catch a **mid-animation frame** — a half-faded dialog, a sliding
  panel, a mask overlay not yet settled over its target — producing a blurred, half-rendered
  image that also differs frame-to-frame across machines (breaking capture reproducibility).
  Disable animations for the capture (Playwright's `animations: 'disabled'` screenshot option on
  the reference engine, the equivalent for your engine, or a reduced-motion /
  `* { transition: none }` override), or wait for the transition to finish; do **not** rely on a
  fixed sleep.
- **Scroll lazy / below-the-fold content into view before a full-element capture.** A
  full-element shot of a list or region that lazy-loads images, virtualizes rows
  (`IntersectionObserver`), or renders skeletons until scrolled will ship **blank or
  placeholder rows** in the parts never scrolled into view — the container's visibility
  assertion still passes, but the captured image below the fold is empty. Scroll the region to
  force-load its content (or assert the narrated rows are actually painted) before the full
  capture.
