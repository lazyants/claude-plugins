# State-variant capture — empty / error / denied

A chapter's default, always-required capture is the **populated** ("happy") state. A page may also
have interesting **variant** states — empty, error, or permission-denied — worth capturing as
labelled variants of the same route. Variant capture is **opt-in**: skip it for a page where the
variant adds nothing over the populated state and prose disclosure already covers the case (see
`references/completeness-gate.md`).

## The three variants are REAL app states

Every variant captured under this reference is a **real rendering** the app produces on its own —
never a synthesized response. This plugin's anti-fabrication discipline extends to state variants: a
screenshot must show what the app actually paints, not a state you invented to illustrate a point.

- **empty** — drive the page with an empty-fixture `storageState` (already-supported seeding, no new
  mechanism) so the app renders its own real empty-list/empty-state UI.
- **denied** — drive the page with a reduced-role `storageState` that lacks the permission; the app
  paints its own real 403/permission-denied UI. The guard needs no change — this is an ordinary
  authenticated read, just as a role that holds the permission would make.
- **error** — drive the page into a **real** error response, reached read-only: a bad-id route
  (e.g. `/items/does-not-exist` resolving to the app's own real not-found screen), a genuinely-staged
  broken seed, or a resource the seeded role truly cannot read (a real 4xx/5xx). The capture guard
  **allows** the GET — it is a read — and the app paints its **own** error UI. Never fabricate a
  response to force an error; if no real error can be staged read-only, see "When a real error
  cannot be staged read-only" below.

## Why route + heading identity is not enough

Route and heading are identical across a page's staged (variant) and un-staged (populated) states —
neither `capture.page_identity_signal` nor the primary heading changes when only the underlying data
does. A forgotten or **reverted** precondition (the empty fixture was not applied, or a prior run
already restored the populated data) therefore still passes ordinary page identity and silently
ships the wrong-but-real state. `references/page-identity.md:127-136` states this rule generally;
state-variant capture is the canonical case it exists for.

The fix is the fail-closed `state` marker on `assertIdentity` (`assets/capture-helpers.playwright.ts`):

- `state.present` — a marker unique to the variant (an empty-state placeholder, an error banner, a
  permission-denied notice), asserted **visible**.
- `state.absent` — the wrong-state marker (e.g. the populated heading, or another variant's marker),
  asserted **NOT visible**. Optional extra strength; use it where a reverted precondition is a real
  risk.

Both are matched with `{ exact: true }` — a substring or case-insensitive `getByText` match is a
false-trip risk the rest of the helpers already guard against (see `dismissModal`'s identity check).

## `state.present` is a first-class readiness anchor, not just an extra check

An error or denied screen typically has **no normal heading**, and its data-load, if any, returns a
non-2xx response that `armApiWait`'s `res.ok()` predicate never accepts. Without a third readiness
signal, `assertIdentity` would have no valid path to certify such a page ready before the shot — it
would either throw on a missing heading/API precondition, or, worse, proceed to shoot before the
variant screen actually painted.

`state.present` closes that gap: it is waited **visible early**, before the heading assertion, and
the readiness precondition is satisfied by `state.present` alone when no `heading`, `waitForApi`, or
`apiReady` applies. Pass **only** `state.present` (no `heading`) for a variant whose normal heading
does not render — asserting both simultaneously (`heading` **and** `state.absent` set to that same
heading text) is self-contradictory and always throws, because the heading assertion and the
wrong-state assertion can never both hold.

## Populated is the default; variants are opt-in

Every chapter must still capture the populated state — that requirement is unchanged. A variant is
worth capturing only when it adds documentation value: the empty state has distinct copy the reader
should know about, a permission-denied screen a role legitimately encounters, an error state end
users will recognize. The per-page state-coverage checklist in `references/completeness-gate.md`
tracks which variants a page has and which are opt-in-skipped, so the coverage decision is visible
at review time rather than made silently.

The optional manifest `states:` field (`assets/capture-manifest.example.yml`) lets an author flag
intended variants for the human manifest review before any capture code runs — see
`references/manifest-discipline.md`.

## When a real error cannot be staged read-only

Some error states genuinely cannot be reached read-only — a 500 you cannot trigger without breaking
a shared fixture or firing a write. In that case:

- Prefer a bad-id / not-found route, a dedicated broken-seed fixture, or a role that genuinely lacks
  read access — any of these produces a real error read-only, and should be exhausted first.
- If none apply, do not fabricate the response. Either document the error state in prose (a
  disclosure per `references/completeness-gate.md`'s disclosure templates) or drop the error variant
  from this chapter's scope, and say so at the manifest review.
- A synthetic forced-error mechanism (fulfilling a request with a fabricated error response) was
  evaluated for this plugin and was **not shipped** — it would add a response-synthesis surface to
  the fail-closed capture guard, which is hard to walk back once in use. If a future version of this
  plugin ships one, this section is where it will be documented; until then, real-state capture or
  prose disclosure are the only supported paths.
