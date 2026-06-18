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
   distinguishing element the chapter is about is actually present and visible.
3. Confirm the loading state is gone — no spinner, skeleton, or "please wait" overlay covering
   the area the screenshot will capture.
4. Confirm the specific element the step narrates is visible — if the step says "click the
   Export button", the Export button must be asserted visible before the click and before the
   shot that documents the click.

If any of these fail, the run must **fail loudly** — never fall back to capturing whatever is on
screen. A wrong screenshot is worse than no screenshot, because the chapter will ship it.

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
