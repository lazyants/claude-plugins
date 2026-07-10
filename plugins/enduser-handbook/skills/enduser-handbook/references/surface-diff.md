# Per-role surface diff (opt-in re-audit)

The surface-enumeration pass in [completeness-gate.md](completeness-gate.md#surface-enumeration-mechanical-first-pass)
runs once, against a single role, and that single-role pass remains the
default for every capture run. When `capture.auth_role_enum` lists more than
one role, you may additionally run the enumeration once per role and diff the
results, to see how the interactive surface itself differs between roles —
which controls a permission gates, not just which data a permission gates.
This is an OPT-IN triage aid, not a replacement for the human classify pass in
completeness-gate.md, and not a new capture-feasibility gate.

[assets/lib/surface-diff.mjs](../assets/lib/surface-diff.mjs) and
[assets/lib/surface-diff.d.mts](../assets/lib/surface-diff.d.mts) are
non-normative reference implementations of the pure diff logic;
[assets/reaudit.example.spec.ts](../assets/reaudit.example.spec.ts) is a
non-normative reference implementation of the per-role Playwright driver. This
document is the normative, engine-agnostic contract; reimplement the driver
for another engine, reusing `surface-diff.mjs` as-is (it has no browser
dependency).

## Where the roles list lives

The roles list — each role's `label` plus its `storageState` fixture path —
lives in the capture spec (`assets/reaudit.example.spec.ts`'s `AUDIT_ROLES`
const), never in the profile. [manifest-discipline.md](manifest-discipline.md)
is explicit that storage-state paths are a spec-level artifact, not a manifest
or profile field, and the existing single-role spec already keeps its
`STORAGE_STATE` const the same way. The profile keeps only
`capture.auth_role_enum` as the role vocabulary: each `label` you add to
`AUDIT_ROLES` must be a member of that enum. Adding a second, role-keyed
profile field for this would create a second home for auth configuration that
can drift from `capture.command` — deliberately avoided.

## Parallel hermetic per-role seeding

Each role's `storageState` fixture must carry the SAME underlying records,
differing only by role/permission — never a fixture that also varies the
underlying data. This is what makes the diff mean something: a count or
membership difference reflects a permission boundary, not a coincidence of
which records happened to be seeded for which role. A non-parallel seed makes
the diff a data-coverage artifact rather than a role comparison, and should
not be trusted for triage.

This is the ROLE axis. [state-variants.md](state-variants.md) covers the
sibling DATA-STATE axis — the same role's surface across populated, empty,
error, and denied states. The two are independent dimensions: this document's
per-role diff holds page state fixed (each fixture is a populated, ordinary
state) and varies only the role; state-variants.md holds the role fixed and
varies the page state. Running one is not a substitute for the other, and
mixing the two axes in a single fixture (seeding one role's populated state
against another role's empty state) breaks the parallel-seeding requirement
above and the diff can no longer be trusted.

## The structural diff key

`structuralKey` (in `surface-diff.mjs`) keys a control on its structural,
PII-free identity — the tuple:

tag / role / name / data-testid

`tag` is the element's tag name, `role` is its own ARIA role attribute
(lowercased), `name` is the developer-set form-field name, and `data-testid`
is the developer-set test id. All four are developer identifiers, never user
data.

Deliberately EXCLUDED from the key: `text`, `aria-label`, `title`, and
`class` — the v1.0.6-broadened label/class fields — and `href`. A cosmetic or
per-seed labelling difference between two roles (the same button rendering
"Save" for one role and "Speichern" for a differently-localized fixture, or an
aria-label that merely differs in wording) must NOT masquerade as a surface
difference; excluding these fields is what keeps the diff about STRUCTURE, not
about incidental label drift. `href` is excluded for a sharper reason: it
commonly embeds a per-seed record id (`/items/5/delete` for one role's seeded
record vs `/items/9/delete` for another role's differently-numbered seeded
record). Including it would FABRICATE a diff between two structurally
identical roles purely because their hermetic fixtures used different record
ids — the opposite of what parallel seeding is for.

## The diff predicate

`diffSurfaces` builds a per-role control count for every distinct structural
key, 0-filled across the FULL declared role set — every role passed in, not
just the roles that happened to contribute a control under that key. A key is
flagged in the diff when its per-role counts are not all equal
(`min !== max` across every role). The 0-fill is what catches MEMBERSHIP
asymmetry (a control present for one role and entirely absent for another) in
addition to COUNT asymmetry (an icon-only collision counting 2 for one role
and 1 for another) — without it, a membership-asymmetric key would simply be
missing from a role's tally rather than present at 0, and the min/max compare
would never see the gap.

Running a single role through `diffSurfaces` is harmless: with one role, every
key's min and max are the same value by construction, so the diff is always
empty. single-role is safe to leave as the default capture path.

## PII & detection limits

- **The machine key is PII-free.** `name` and `testId` are developer
  identifiers. Only the display `label` field (built by `matrixLabel`, the
  same fallback chain the surface audit itself uses) carries residual PII,
  under the SAME boundary as the existing surface audit: run only against
  seeded, non-PII data, and scrub the human classify pass before committing.
  This diff adds no new capture surface — it consumes an already-suppressed
  inventory.
- **Weak key for label-less, testid-less controls.** Two plain icon buttons
  with no aria-label, no name, no testid collide on the same key
  (`["BUTTON", null, null, null]`). The count-inequality predicate still
  catches the UNBALANCED case (2 vs 1). Add a `data-testid` to destructive
  icon-only controls to close this gap.
- **An equal-count directional swap is undetectable — stated at full
  strength.** If the admin role exposes `{Edit, Delete}` and the external role
  exposes `{Edit, Archive}`, and all four controls are icon-only with no
  role/name/testid, both roles report a count of 2 for the SAME collided key
  (`["BUTTON", null, null, null]`) — `min === max`, so NO diff is reported,
  even though the external role lost a destructive capability (`Delete`) and
  gained a different one (`Archive`). This is a deliberate, conservative
  UNDER-REPORT, never a fabrication: the diff can fail to flag a real
  difference, but it will never invent one that is not there. Add a
  `data-testid` to destructive controls specifically to close this class of
  gap.

## Relationship to the audit's own label chain

The display `label` field is produced by `matrixLabel`, imported from
[control-inventory.mjs](../assets/lib/control-inventory.mjs) rather than
duplicated here, so the per-role diff can never drift out of sync with the
surface audit's own fallback chain (see
[completeness-gate.md](completeness-gate.md#surface-enumeration-mechanical-first-pass)
for the chain itself). If the label chain changes, the diff's labels change
with it automatically; there is exactly one place that decides how an
unlabelled control is displayed.
