# Capture-spec helpers — the importable contract

The capture-safety, page-identity, and masking rules are prose discipline. Re-porting them by
hand into every chapter's capture spec is where they drift. This file is the **engine-agnostic
contract** for a small set of helpers that encode those rules once, so a project's capture specs
import them instead of re-implementing them.

This is a contract, not the code. The normative, engine-agnostic rules live in
`capture-safety.md` and `page-identity.md`; this file says only *what each helper must
guarantee*. A **non-normative reference implementation** for the Playwright reference case ships at
`../assets/capture-helpers.playwright.ts` and `../assets/surface-audit.playwright.ts` — reimplement
the driver glue for another engine; the engine-neutral `../assets/lib/*.mjs` helpers are reused
as-is. The reference doc is normative; the `*.playwright.*` asset is one implementation.

## What each helper must guarantee

- **Capture guard** — installed at the browser-context level *before any page is created*, with the
  context configured so service-worker traffic cannot bypass it. It classifies **every request the
  engine surfaces to its interception handler** in a strict order: deny-patterns always block;
  long-lived reads (server-sent events) and analytics beacons block unless explicitly admitted; a
  single read predicate (`classifyRequest`) may admit an otherwise-blocked read (a GraphQL **query**,
  not a mutation); plain GET/HEAD reads pass; **every non-GET/HEAD request left unadmitted fails
  closed (blocked + recorded)**. It exposes one assertion that throws if any dangerous/blocked
  request fired during capture. This is **defense-in-depth, not permission to click ambiguous
  controls** — the human capture-safety classification still governs every click. There is exactly
  **one** read escape hatch (`classifyRequest`) and exactly **one** opt-in allowlist:
  `allowBeacons: true` admits **every** request the engine types as a `ping`, to any origin, GET and
  POST alike (measured — a cross-origin POST beacon returns `allow`/`beacon-allowed`), so it is a
  broad beacon allowlist, not a narrow one; `denyPatterns` still win over it. There is no write
  allowlist and no origin allowlist. WebSockets are blocked *without connecting*; an engine that
  cannot block a socket (only observe it) must fail
  at install time, not silently open. The ordered decision is a **pure function**
  (`../assets/lib/capture-guard-policy.mjs`, `decideRoute`) so its branch order is unit-tested, not
  just grep-asserted. The end-of-run assertion **drains a short quiet period** before checking, so a
  delayed beacon/fetch fired after the last interaction is still caught.

  **What the GET/HEAD allow does NOT check — the guard is not fail-closed on reads.** Once a request
  reaches the general GET/HEAD allow — past the deny, benign, SSE and beacon blocks above it — a GET
  or HEAD is admitted **unconditionally**: the **origin is never
  examined** (`decideRoute` takes no base URL or app origin), so a GET to a third-party host passes
  exactly like a same-origin one. The only built-in brake on a *writing* GET is a **fixed 16-verb
  token list** matched against the URL path and query (`DANGEROUS_VERB_SET` — 13 English, 3 German).
  That list is not, and cannot be, an enumeration of destructive route vocabulary: a GET to
  `/orders/42/confirm`, `/reports/publish` or `/users/7/impersonate` is **admitted**. So it is *not*
  true that a destructive GET the author forgot to deny-list still fails closed — that holds only
  when the forgotten verb happens to be one of the 16. Every other writing GET must be listed in
  `denyPatterns` or refused by `classifyRequest` — and that refusal is **silent**: the predicate's
  only refusing verdict is `'benign'`, which blocks the request but is excluded from
  `assertNoDangerousHits()` by design, so no return value means "block this GET **and** count it
  dangerous". For a writing GET outside the 16, `denyPatterns` is the only lever that both stops the
  request and fails the run. (Tracked as issue #470.)

  **Redirect hops are DETECTED, not intercepted.** A request the browser issues itself to follow a
  3xx `Location` never reaches the interception handler — measured against the real engine on
  playwright-core 1.61.1 and 1.62.1, a `GET /reports/monthly` → 302 → `/orders/42/finalize` chain
  reached the server in full while the handler saw only the first request. The guard therefore runs a
  **second, audit-only channel** over the engine's request-observation event: every hop is
  re-classified by the same ordered policy using the **hop's own method, URL and body** (307/308
  preserve both the method and the body — so a body-shaped `denyPattern` reaches a hop exactly as it
  reaches a fresh request; 301/302/303 may downgrade a POST to a GET), the whole chain is logged for
  inspection, and any hop the policy would have blocked — **except** one the project's own
  `classifyRequest` returns `'benign'` for, which is reported in the chain only — is pushed into the
  dangerous ledger so the end-of-run assertion **fails loudly**. Note that a `'benign'` hop is a
  weaker claim than a `'benign'` blocked request: the blocked one never fired, the hop already did.
  This is detection, not prevention — the browser has already sent the hop, so a failure naming a
  `redirect-hop:` reason means a live request **fired**, not that one was
  stopped. Admitting a request through `classifyRequest` never admits its hop target: the hop is
  classified on its own, and the predicate is told which request it came from. (Issue #471.)

  **`classifyRequest` is one predicate with two non-`undefined` verdicts — `'read'` and `'benign'`
  do opposite things.** `'read'` **ADMITS** (allows) the read escape: the otherwise-blocked GraphQL
  query is let through. `'benign'` **BLOCKS** the request — it never fires — but EXCLUDES it from
  the dangerous-hits assertion, so known-harmless dev telemetry (a laravel-boost `/_boost/` log
  POST, a Sentry beacon) does not false-trip `assertNoDangerousHits()` on any page that
  console-logs. Everything else (any other return, including a stray truthy) is **not a verdict at
  all** — the request simply falls through to the ordered default, which is **fail-closed (blocked +
  recorded as dangerous) only for a request that is not a plain GET/HEAD**; a GET/HEAD that reaches
  the general allow is still admitted unconditionally, as above. **An SSE GET never reaches it** —
  `[guard:eventsource]` blocks a stream the predicate did not admit, so returning `undefined` for an
  event-source endpoint blocks it rather than passing it. **A beacon never reaches it either** —
  `[guard:beacon]` also decides before `[guard:classify-read]`, so a request the engine types as
  `ping` is blocked for GET and POST alike (measured) even when the predicate returns `'read'`; the
  only thing that admits one is the `allowBeacons: true` opt-in above. Note the asymmetry: `'read'` allows,
  `'benign'` blocks — they are not "both block". `classifyRequest` must be **total**: return
  `undefined` for anything it does
  not recognize and never throw (the guard now consults it for beacon/SSE requests too). There is
  still **NO write allowlist** — `'benign'` silences a block, it does not permit a write.

  **The shipped `classifyRequest` is GraphQL-only.** `../assets/lib/graphql-read-classifier.mjs`'s
  `classifyGraphqlRead` admits only a POST carrying an inline, single-operation GraphQL **query**; a
  project whose reads are REST/RPC POST calls (Django/DRF, JSON-RPC) has every such read fail closed,
  with no built-in admit path. That is not a gap to patch centrally — the project supplies its own
  `classifyRequest` that recognizes its own read shape, returning `'read'` only for unambiguous,
  side-effect-free reads and `undefined` otherwise, the same fail-closed contract the shipped
  classifier follows. No code change is required: the guard already accepts a custom
  `classifyRequest`.

- **Identity assertion** — before every shot, prove the page is the one the manifest declares: the
  route matches, the loading state is gone, and either the awaited response arrived
  (client-rendered) **or** the primary heading/DOM is visible (server-rendered — a first-class
  case, not a fallback). Fail loudly; never shoot whatever is on screen. An optional **state
  marker** (`state.present` / `state.absent`) is a third, first-class readiness+identity path for a
  state-variant capture (empty/error/denied) whose normal heading may be absent: `present` is
  waited visible as the readiness anchor, `absent` asserts the wrong-state marker is not visible;
  both matched `{ exact: true }`. See `references/state-variants.md`.

- **Region / viewport capture** — element-scoped for a single component; viewport for long
  unpaginated lists that overflow the element frame. An opt-in `{ maxHeight }` clamps a
  runaway-height region: when set and the element's rendered height exceeds it, the helper
  captures only the top `maxHeight` (via a temporary CSS height clamp, restored after) — content
  below the clamp is hidden, so **paginate** the capture in sections or **disclose** the truncation
  in prose. It is a guard against a layout-bug height balloon (a modal ballooned to ~82,000px), not
  a tall-capture solution; default behavior is unchanged when `maxHeight` is omitted.

- **Bleed-free oversize-overlay capture** — a dedicated helper for an overlay/region **taller than
  the viewport** takes a **single viewport-clipped** shot (scroll the element to the top, clip to
  the viewport) instead of `captureRegion`'s element-screenshot path, which scroll-stitches an
  oversize element together and can bleed a `position:fixed` page-behind at a shifted offset across
  the seam. The clip **throws on any horizontal clipping** (a silently cropped shot would hide real
  content) and on an empty vertical intersection; vertical overflow alone is not an error — it
  reports whether the full element fit **after scroll-to-top**, so the caller/doc **discloses any
  remainder in prose**, mirroring `maxHeight`'s truncation discipline above. Stability rests on the
  engine's own animation-freeze mechanism for the shot interval, plus a bounded, **fail-closed**
  wait for the caller's own open/slide transition to settle first — it **throws** rather than ship
  a mid-animation frame. Publish is atomic (verified buffer → temp file → rename) so a file at the
  target path is always trustworthy proof, never a rejected/partial frame.

- **Modal open / dismiss** — assert the dialog's identifying text first, then dismiss via **Escape
  first**, falling back to a named negative/cancel control. **Never** the primary/first button,
  which can be the destructive one.

- **Mask-and-assert** — scope the shot *and* the leak scan to **exactly what the
  screenshot frames** — the **opaque inner dialog** for a modal/element shot, the **document
  root** for a full-viewport shot, never a node narrower than the frame. **Mask
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
  Both passes recurse into **open** shadow roots. **Six** things the automated scan does **not**
  cover — they all fall to the human eyeball-the-frame step as the backstop: **closed**
  shadow roots (inaccessible to script — mask inside the component or open the root for capture); **CSS
  pseudo-element content** (`::before`/`::after` `content:`, painted into the shot but not a DOM text
  node); **a broken or failed `<img>`'s `alt` text** (the browser paints it into the frame as
  replacement-rendering, but it is **not** a DOM text node — so the text/value/placeholder corpus
  misses it exactly as it misses pseudo-content; a successfully loaded image paints no `alt`);
  **genuinely non-rendered attributes** (`title`/`aria-label`, never painted into a static
  screenshot); **the content of a same-origin `<iframe>`**, which is a different class from the
  four above because it *is* ordinary DOM text — just in another document; and **anything a
  `<canvas>` paints**, a class of its own again — a bitmap with no DOM representation at all.
  Take the framed case first: neither the mask nor the scan crosses a document boundary (a tree walk
  rooted in the parent stops at the `<iframe>` element, which has no text children), while the
  screenshot composites the child document's pixels. Framed content is therefore photographed but
  never masked and never scanned, and the dangerous half of that is **silent**: PII the author did
  *not* list has nothing to mask, no text node to collect and no pattern to match, so neither pass
  has anything to object to. That is why this carve-out
  is **enforced, not merely disclosed**: the helper counts every element in the region that hosts a
  **nested browsing context** — `<iframe>`, `<frame>`, `<object>`, `<embed>`, all of which can load a
  document of their own on exactly these terms — and **throws** when it finds any, checked **before**
  the coverage assert, so a framed region is named as the cause rather than misreported as selector
  drift. Mask or remove the framed content before the shot, scan it yourself per frame, or keep it
  out of the captured region.
  **What the refusal can see, exactly:** the light DOM and **open** shadow roots of the subtree it
  was handed, at the moment it is called. A frame inside a **closed** shadow root, a frame painted
  over the captured rectangle from **outside** that subtree, and a frame attached **after** the call
  (a spec may take more than one shot off a single mask) are all still uncounted, and stay the human
  eyeball-the-frame step's job. The selector is deliberately unqualified rather than
  `object[data]`/`embed[src]`, because the count is taken when the helper runs and the pixels are
  taken later: an element holding no document at that moment can still be holding one in the shot.
  So an `<object>` carrying no document at all is refused too — one `allowUnscannedFrames: true` is
  the whole cost of that over-refusal, and it is the correct direction for a PII gate.
  **Past the single opt-out `allowUnscannedFrames: true`, the old behaviour is what remains**: a
  selector matching only inside the frame catches nothing and trips the mask-**coverage** assert,
  while PII the author never listed ships in the PNG with the run green. Take the opt-out only once
  you have proven those documents carry no PII. (Issue #472.)
  **The `<canvas>` carve-out is enforced the same way, with different remedies.** A `<canvas>` is
  composited into the PNG exactly like everything else, but what it paints is a **bitmap**:
  `fillText` output is not a DOM text node, not a form-control value, and not reachable by any
  selector. So it is silent in both directions, the way a framed document is — there is nothing for
  `selectors` to match (listing the `<canvas>` itself only sets its `textContent`, and a canvas's
  children are fallback content that paints nothing), nothing *of what was painted* for the leak
  patterns to fire on, and the coverage assert stays satisfied by whatever *was* listed. Be precise
  about that middle one: a canvas's **fallback** text is an ordinary text node and **is** scanned —
  it is simply not what the canvas shows. "A canvas has no text" is a false universal; the true
  statement is that the painted pixels are in no corpus either pass can read. The helper therefore counts
  every `<canvas>` in the region — light DOM and **open** shadow roots, the region itself
  included — and **throws** unless the caller passes `allowUnscannedCanvas: true`. A canvas the
  caller *did* list in `selectors` still counts: tagging it removes it from the scan without
  changing pixels no mask could overwrite. What differs from the framed case is the remedy — there
  is no "scan it yourself per canvas", because a canvas hosts no document — there is no second
  corpus to run the mask and the scan over, and the painted pixels are not text in any corpus.
  Clear or overwrite the canvas before the shot, replace it with a placeholder element, or
  keep it out of the captured region. The refusal names `<canvas>` only: pixels an `<img>` or a
  `<video>` brings into the frame are photographed and unscanned as well, and stay the human
  eyeball-the-frame step's job. The case this is for is a document preview rendered to a canvas
  (PDF.js renders every page that way), a canvas-mode data grid, or a signature pad — where a whole
  document body, not a bounded label, rides into the shot. (Issue #565.)

## The spec skeleton

Every chapter capture spec wires the same shape: create the context (service workers blocked,
seeded auth — never a live login) → install the guard **before** the first page → assert identity →
optionally run the surface-enumeration pass → per step: assert the element, capture the region or
open+dismiss the modal, mask where PII appears → assert no dangerous hits at the end. The output
dir is derived via `chapterAssetDir` (`../assets/lib/chapter-paths.mjs`), which is group-aware
(issue #19) — never a hardcoded `output_dir/<slug>` literal. The reference spec at
`../assets/capture.example.spec.ts` shows this end to end.

## Surface enumeration

The mechanical first pass for the coverage matrix (see `completeness-gate.md`) enumerates the
**live DOM**, capturing every interactive trigger verbatim — text, title, aria-label, href, role,
test id, `className` — including **icon-only** controls, and **never filtering by text presence**.
`className` is captured for the destructive-control classification (icon classes such as
`glyphicon-trash`/`fa-trash`) and is covered by the **PII boundary of the mechanical pass** in
`completeness-gate.md` — scrub it in the human pass if an app encodes record/user slugs into class
names. The reference impl at `../assets/surface-audit.playwright.ts` factors per-control extraction
into a browser-agnostic module (`../assets/lib/control-inventory.mjs`) so the "icon-only control
dropped" regression is unit-testable. Enumeration is a hint; the human classify/status pass in
`completeness-gate.md` is authoritative.
