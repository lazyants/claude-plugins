# Capture-spec helpers — the importable contract

The capture-safety, page-identity, and masking rules are prose discipline. Re-porting them by
hand into every chapter's capture spec is where they drift. This file is the **engine-agnostic
contract** for a small set of helpers that encode those rules once, so a project's capture specs
import them instead of re-implementing them.

This is a contract, not the code. The normative, engine-agnostic rules live in
`capture-safety.md` and `page-identity.md`; this file says only *what each helper must
guarantee*. A **non-normative reference implementation** for the Playwright reference case ships at
`../assets/capture-helpers.playwright.ts` and `../assets/surface-audit.playwright.ts` — fork it for
other engines. The reference doc is normative; the `*.playwright.*` asset is one implementation.

## What each helper must guarantee

- **Capture guard** — installed at the browser-context level *before any page is created*, with the
  context configured so service-worker traffic cannot bypass it. It intercepts every request and
  classifies it in a strict order: deny-patterns always block; long-lived reads (server-sent
  events) and analytics beacons block unless explicitly admitted; a single read predicate
  (`classifyRequest`) may admit an otherwise-blocked read (a GraphQL **query**, not a mutation);
  plain GET/HEAD reads pass; **everything else fails closed (blocked + recorded)**. It exposes one
  assertion that throws if any dangerous/blocked request fired during capture. This is
  **defense-in-depth, not permission to click ambiguous controls** — the human capture-safety
  classification still governs every click. There is exactly **one** read escape hatch
  (`classifyRequest`); no broad write/stream/origin allowlists. A **built-in dangerous-verb path
  block** (delete/destroy/remove/disable/… in any URL segment) blocks even a GET, so a destructive
  GET the author forgot to deny-list still fails closed. WebSockets are blocked *without connecting*;
  an engine that cannot block a socket (only observe it) must fail at install time, not silently
  open. The ordered decision is a **pure function** (`../assets/lib/capture-guard-policy.mjs`,
  `decideRoute`) so its branch order is unit-tested, not just grep-asserted. The end-of-run
  assertion **drains a short quiet period** before checking, so a delayed beacon/fetch fired after
  the last interaction is still caught.

- **Identity assertion** — before every shot, prove the page is the one the manifest declares: the
  route matches, the loading state is gone, and either the awaited response arrived
  (client-rendered) **or** the primary heading/DOM is visible (server-rendered — a first-class
  case, not a fallback). Fail loudly; never shoot whatever is on screen.

- **Region / viewport capture** — element-scoped for a single component; viewport for long
  unpaginated lists that overflow the element frame.

- **Modal open / dismiss** — assert the dialog's identifying text first, then dismiss via **Escape
  first**, falling back to a named negative/cancel control. **Never** the primary/first button,
  which can be the destructive one.

- **Mask-and-assert** — scope the shot *and* the leak scan to the **opaque inner dialog**. **Mask
  first, then scan** — overwrite text nodes, form-control values **and `placeholder` text** (for a
  `<select>`, the rendered *label* of **every option** — all options, not just the selected ones: a
  `<select multiple>`/`[size>1]` renders its unselected options too, so an unselected option label
  with PII would otherwise ship). Tag each masked element, then scan the **whole subtree EXCLUDING
  the masked elements** so PII the author forgot to list is still caught and a correctly-masked
  target never false-positives. The scan corpus is rendered DOM text + form-control values +
  placeholders. Exclude masked nodes by identity (a marker attribute), **not** by string-stripping
  the mask placeholder — stripping fuses an adjacent unmasked value into a false negative. Build the
  scan string by **joining per-node values with a newline** (not one concatenated `textContent`,
  which fuses neighbouring cells into false tokens), then fail if any leak pattern matches **or** if
  the matched-mask count differs from the expected count (fail-closed coverage for unmatchable PII).
  Both passes recurse into **open** shadow roots. Three things the automated scan does **not** cover
  — like closed shadow roots, they fall to the human eyeball-the-frame step as the backstop: **closed**
  shadow roots (inaccessible to script — mask inside the component or open the root for capture); **CSS
  pseudo-element content** (`::before`/`::after` `content:`, painted into the shot but not a DOM text
  node); and **non-rendered attributes** (`title`/`aria-label`/`alt`, which are not painted into a
  static screenshot).

## The spec skeleton

Every chapter capture spec wires the same shape: create the context (service workers blocked,
seeded auth — never a live login) → install the guard **before** the first page → assert identity →
optionally run the surface-enumeration pass → per step: assert the element, capture the region or
open+dismiss the modal, mask where PII appears → assert no dangerous hits at the end. The reference
spec at `../assets/capture.example.spec.ts` shows this end to end.

## Surface enumeration

The mechanical first pass for the coverage matrix (see `completeness-gate.md`) enumerates the
**live DOM**, capturing every interactive trigger verbatim — text, title, aria-label, href, role,
test id — including **icon-only** controls, and **never filtering by text presence**. The reference
impl at `../assets/surface-audit.playwright.ts` factors per-control extraction into a
browser-agnostic module (`../assets/lib/control-inventory.mjs`) so the "icon-only control dropped"
regression is unit-testable. Enumeration is a hint; the human classify/status pass in
`completeness-gate.md` is authoritative.
