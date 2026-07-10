# Capture safety — never fire the live action

Capture runs against the real running app. The app is wired to live integrations and real
data — your screenshots are evidence of what the UI looks like, not a license to make the
app *do* anything. This file is the discipline. It applies whether or not the profile lists
any examples.

## The principle

When you drive the app to capture a feature, you capture **view-state only**. You open the
page, you open the overlay, you read the labels — and you stop there. You never click a
control whose effect is irreversible, leaves the system (external API), mutates production
data, or sends something to a real customer. The function is still covered: you document
the open-state and disclose what the button would do. The ban is on *triggering* it, not on
documenting it.

A function the role can reach but cannot safely fire is **not** dropped from the chapter.
It is captured at its read-only/open state, and the prose tells the reader what the
control does and that the screenshot stops short of pressing it. Silent omission is a
defect; intentional disclosure is correct.

## Reading `capture.live_action_examples`

The profile's `capture.live_action_examples` is a list of concrete, project-specific
actions the running app exposes that must never be fired during capture. You read this
list before you start capturing and you keep it in mind as you navigate. Treat the list as
illustrative, not exhaustive — its purpose is to teach you the *shape* of dangerous
actions in this project so you can recognize the next one even when it is not listed.

If the list is empty you still apply the principle. An empty list means the project team
has not enumerated examples yet; it does not mean the app has no live paths. Default to
caution: any button labeled with a verb of dispatch, replay, send, confirm, commit,
delete, sign, submit-to-external, irrevocable-anything is suspect until proven read-only.

Before clicking any control in a capture run, you ask yourself: is this control in
`capture.live_action_examples`, or shaped like one of those entries? If yes, you stop at
modal-open and you do not click further.

## The PII rule — `capture.pii_categories`

The profile's `capture.pii_categories` lists categories of personally identifying or
sensitive content that must not appear in screenshots without explicit user sign-off. You
read this list and you treat it as a screenshot filter, not just a navigation hint.

Three options when a PII category would appear in your shot, in order of preference:

1. **Skip the shot.** Document the function in prose with a one-sentence disclosure that
   explains why no screenshot is included.
2. **Mask the PII.** Replace identifying values with placeholders — names, addresses,
   account numbers, document contents, scanned identity material. Prefer masking
   **reproducibly in the capture step** (mutate the rendered values just before the shot),
   not by hand-editing the saved PNG, so the mask is reproduced on every re-capture. The
   mask must be visually obvious so a reader does not mistake masked content for real data.
   See **Mask reproducibly, then prove the mask held** below.
3. **Ask the user to sign off.** Only when masking is impractical and the screenshot is
   essential. The sign-off is per-shot, in writing, and recorded in the chapter's
   commit/PR notes. Do not assume a previous sign-off carries forward to a new shot.

If `capture.pii_categories` is empty you still apply the principle for any obviously
sensitive content (real names, real financial data, real medical or legal documents).

### Mask reproducibly, then prove the mask held

When you choose option 2, three rules make masking trustworthy. They are engine-agnostic —
the exact API (mutating the rendered page, taking the element shot, asserting) lives in the
project's capture specs, not here.

- **Mask inside the capture step, not by hand.** Mutate the rendered content — text nodes
  AND form-control values (text inputs, textareas, **all options** of a dropdown — not just the
  selected one, since a multi-select renders unselected options too) AND `placeholder` text, not
  only visible text — immediately before the screenshot, so the mask is reproduced on every
  run. A PNG edited by hand silently reverts the next time someone re-captures.
- **Add a fail-closed leak-assert.** After masking, programmatically assert that no real
  identifier survived in the captured content and FAIL the run if one did (strip your own
  placeholder first so it is not re-flagged). Build the string you scan from **individual
  text nodes plus form-control values, joined by a separator that cannot occur mid-token**
  (a newline) — not one concatenated `textContent`. A joined `textContent` fuses adjacent
  cells, so unrelated neighbours match a pattern that neither contains alone (an order
  number butting against the next cell reads as an IBAN) — a false leak that wastes a run
  and, worse, can hide a real one. This is the PII regression test: a later UI change that
  surfaces a new address breaks the run instead of shipping a leak.
- **Scope the mask AND the assert to what the SCREENSHOT frames — not to the element you
  think you are shooting.** A full-viewport overlay (a modal or backdrop rendered fixed over
  the page) has a bounding box that spans the whole viewport, so an element-screenshot of the
  *container* also captures the page showing through its transparent area — and a leak-assert
  scoped to that container's own subtree is blind to the sibling content bleeding through
  behind it. Screenshot the opaque inner dialog box, and run the assert over that same node.
  The rule is **bidirectional** — scope must *equal* the captured frame, never
  narrower. The inverse case: a deliberate **full-viewport or full-page**
  screenshot (`captureViewport` / `page.screenshot`) frames the whole viewport,
  **including app chrome** — a header showing the logged-in user's name, an
  account sidebar. Scoping the mask and assert to an inner dialog or region here
  leaves that framed chrome scanned by nothing and the name ships.
  `maskAndAssert` runs over whatever locator you hand it, so for a **full-viewport
  or full-page** shot you must scope it (and the leak-scan) to the **document
  root** (`:root`/`body`), not a `getByRole('dialog')` node — otherwise a
  non-modal capture gets no automated scan at all. A `captureRegion` shot is
  element-scoped, so hand `maskAndAssert` that **same region locator** — the
  scan still equals the frame.
- **When the PII has no detectable pattern, assert mask COVERAGE, not just absence.** The
  fail-closed leak-assert above can only catch PII it can *match* — an e-mail regex, a known
  internal domain. Free-form identifiers — personal names, customer/account ids, opaque record
  hashes — have no such pattern, so the leak-assert is blind to them: if the mask silently
  misses a target (a column header renamed, a selector drifted), nothing fails and the real
  value ships in the screenshot. For that class the *mask itself* must be fail-closed — have it
  report how many targets it matched and assert that count equals the number you intended to
  mask, so a missed target throws instead of leaking. Use both together: pattern-matchable PII
  is caught by the leak-assert, unmatchable PII by the coverage assert.

Automated asserts cover the DOM subtree; your eye covers the frame. **Always eyeball masked
and confirmation-dialog shots before publishing** — that is how the bleed-through case above
gets caught when the subtree-scoped assert passes but the frame still leaks.

## Disclosure in prose

When the chapter covers a live or irreversible function, the prose names the action and
its effect, then names the limit of the screenshot. Two patterns, both required as the
situation calls:

- **Read-only function:** describe what the reader sees and what they can do. No
  disclosure needed beyond the normal step text.
- **Live/irreversible function:** describe what the control does, then state that the
  screenshot stops at the open-state. Example shape: "The {{action_label}} button sends
  the record to {{external_system}}. The screenshot below shows the dialog at open; the
  send is not performed in capture." Replace placeholders with the project's real label
  and the prose register from the profile's `language.register`.

The reader learns the function exists and what it does. The capture run never causes the
side-effect. Rule 6 (completeness) is satisfied; the safety rule is satisfied.

## Screenshot the warning dialog, not the after-state

For destructive or irreversible actions the app usually shows a confirmation dialog —
"Are you sure?", "This cannot be undone", a typed-confirm field. **Capture the warning
dialog**, not the post-confirm screen. The warning is the meaningful UI for the reader:
it tells them what they are about to do and what the system thinks the consequences are.
The post-confirm state requires you to actually fire the action, which is the exact
thing you must not do.

Apply the same pattern to multi-step send flows: capture the final-review screen with
the "Send" button visible and labeled; do not capture the "Sent" confirmation.

**Dismiss via the safe control, and pin the selector to it.** Capturing the dialog means
you then have to close it, and the close click is itself a hazard: the dialog has a
destructive button that fires the action and a safe one that backs out. Click the safe one —
and select it by the *negative* (the non-destructive / non-primary button) or by its cancel
label, never as "the primary button" or "the first button in the dialog", which can resolve
to the destructive control depending on the app's button order. Assert the dialog's own
identifying text before you click, so you are dismissing the dialog you think you are.

Prefer the keyboard **Escape** key — it is the version-agnostic safe cancel. Pressing
Escape backs out of the dialog without resolving any button selector, so it cannot
accidentally hit the destructive control. A framework's cancel handle (e.g. a bootbox
`data-bb-handler="cancel"` attribute) is version-specific and may not exist in the installed
version, so do not pin to a guessed cancel selector; press Escape first and fall back to the
negative/cancel-label button only if Escape does not dismiss. The importable helper contract
for this is in `capture-spec-helpers.md`. That contract is the normative, engine-agnostic
rule; `../assets/capture-helpers.playwright.ts` is a
**non-normative reference implementation** for the Playwright reference case — reimplement the
driver glue for another engine; the engine-neutral `../assets/lib/*.mjs` helpers are reused as-is.

For success-toast screenshots (a different case — a reversible mutating action whose
outcome the reader needs to see), only fire the action when the side-effect is safely
reversible *and* the data state is staging/seed, never production. If in doubt,
screenshot the dialog at open and disclose the outcome in prose.

## Auto-save-on-input fields are observe-only

Some fields persist on every keystroke — a notes/comment box that saves on each input
event, an inline-edit cell that commits on blur, a toggle that writes immediately. These
are **mutating actions with no Save button**, so "capture the open form, just don't submit"
does not protect you: typing a single character *is* the write, and it corrupts the
synthetic record mid-run, so later steps capture the mutated state. Treat them as
observe-only — seed the field with representative content beforehand and capture it as-is;
never type into, clear, or toggle them during capture. When you classify a function's
side-effect (Rule 3), check specifically for persists-on-input behaviour, not only
persists-on-submit.

## Synthetic seed data must be hermetic

When you seed synthetic data to make an overlay non-empty, the seed runs against the real
local app, and creating a record can fire the same side-effects the UI would. Model
observers, lifecycle hooks, and event listeners on the seeded models may send e-mail, queue
a job, broadcast, or call an external API on create/update — even though you never touched
the UI. A "local-only" seed can still hit a live integration this way. Before running a seed
during capture, confirm it is guarded to the local environment **and** that no hook on the
seeded models performs an external send — or neutralise the outbound layer (fake the
HTTP/queue/mail transport) for the seed run. The seed must only insert rows; it must not
send.

## Quick self-check before each click

Before you click any button during capture, run the checklist:

1. Is this control listed in or shaped like `capture.live_action_examples`?
2. Does its label contain a dispatch/send/commit/delete/sign verb?
3. Would the click hit an external system, a real customer, or mutate non-reversible
   state?
4. Would the resulting screenshot expose anything from `capture.pii_categories`?
5. Does the target return an error / 500 because a required file, record, or
   integration is absent in the seeded env?
6. Does the control render or send a LIVE document to an external system?

If any answer is yes, stop at the current state, capture it, and disclose the action in
prose. Resume navigation by closing the dialog or backing out — not by clicking through.
For the disclosure sentence, fill in one of the "Disclosure prose templates" in
`completeness-gate.md`.
