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
- **For long unpaginated lists, capture the viewport, not the full element.** A full-element
  screenshot of a list that renders all rows (no pagination) produces an unusably tall image —
  a 100-row table becomes a strip nobody can read. Capture the **viewport** (the visible
  window) for these, so the shot is a normal screen the reader recognizes. Reserve
  full-element capture for short, bounded elements.
- **For a region whose height can balloon (a tall modal or list), cap it.** Pass
  `captureRegion`'s `{ maxHeight }` to clamp the rendered height; content below the clamp is
  hidden, so paginate the capture in sections or disclose the remainder in prose. See
  `capture-spec-helpers.md`.
