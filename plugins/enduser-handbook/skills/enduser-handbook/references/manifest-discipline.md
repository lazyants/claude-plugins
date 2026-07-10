# Manifest discipline

A capture manifest is the human checkpoint between feature-surface discovery and
any automated capture run. You write it, the user reviews it, *then* you write
capture code. Skipping the review step is how handbooks ship the wrong chapters,
the wrong roles, and screenshots of pages nobody asked for.

## Why the manifest exists

The manifest is a single human-readable enumeration of every chapter you intend
to produce, with — per chapter — the fields a human can audit in under a minute:

- `slug` — English kebab-case; becomes the chapter filename.
- `title` — the chapter heading in the project's language (from `language.code`).
- `route` — the in-app path the capture script will visit.
- `role` — one value from `capture.auth_role_enum`; which logged-in user the
  capture runs as.
- `glossary_terms` — terms this chapter introduces or relies on; cross-checked
  against the glossary discipline. This list and the chapter frontmatter's
  `glossary_terms` are **two separate lists that MUST be kept in sync** — the
  completeness gate checks them against each other (see
  `completeness-gate.md`). Renaming a term in one without the other is exactly
  the kind of drift the manifest review exists to catch.
- `steps[]` — ordered list of `{ id, label, action?, screenshot }` covering the
  flow the chapter teaches.
- `states` — optional; enumerates the state variants (beyond the default
  populated capture) this chapter intends to capture — empty / error / denied.
  Every variant is a **real** app state, never a synthesized response. See
  `references/state-variants.md` and the state-coverage checklist in
  `references/completeness-gate.md`.

That enumeration is what the user reviews. They can immediately catch: a feature
you invented, a route that no longer exists, a role that can't actually reach
the page, a step that fires an irreversible action, a glossary term you spelled
wrong, an ordering that doesn't match the running UI. None of those are caught
once the capture run has already produced 200 PNGs.

You always halt for review *before* writing any capture spec. No exceptions —
not "small chapter", not "obvious flow", not "we already discussed it verbally".

## The manifest is engine-agnostic

The shape above is what every project's manifest carries. The example file at
`assets/capture-manifest.example.yml` documents this shape (do not duplicate its
contents here; refer to it by name when you draft a new project's manifest).

Engine-specific fields — anything that only makes sense to one capture tool —
are **profile-controlled, not baked into the manifest schema**. The key example
is the page-identity signal: how the capture script knows the page has finished
loading before it screenshots. That signal is described in free English in
`capture.page_identity_signal` and elaborated in `references/page-identity.md`.
The manifest stays clean; the engine wiring lives wherever the project's
capture command runs.

If a project uses Playwright, its manifest may carry a `waitForApi` field
alongside each chapter — that's fine, it's engine-specific *optional* metadata
the project's capture spec consumes. Other engines use their equivalent or omit
the field entirely. The example asset marks such fields explicitly as
engine-specific so other projects don't copy them blindly.

## Reading roles from the profile

The `role` field on each manifest chapter MUST be a value from
`capture.auth_role_enum`. You do not invent roles. You do not use a role that
isn't enumerated. If the project needs a new role, the profile changes first.

For some chapters the role alone is not enough — a feature may only render for
users with an extra capability beyond their role assignment. That's what
`capture.role_flags` is for: a map from role name to a list of capability
flags. When you draft a chapter for role `R`, you check `capture.role_flags[R]`
and note in the manifest comment which flags the capturing user must hold.

A flag granted does NOT mean the role has the underlying data. It means the
control renders. Whether the page shows real content for that user is a
separate concern handled by the capture-safety and page-identity references.
Note the distinction in the manifest review so the user can flag it.

## The discipline: no capture code before review

The order is fixed:

1. You read the feature surface from `stack.backend.route_globs` and
   `stack.frontend.page_globs` (running-UI rule applies — see
   `references/running-ui-source.md`).
2. You draft the manifest: every chapter, every step, every role, every
   glossary term, in the shape above.
3. You present the manifest to the user and halt for review.
4. The user accepts, edits, or rejects entries. You revise until accepted.
5. *Only then* you write capture specs against the accepted manifest, into the
   directory at `capture.capture_specs_dir` (alongside the manifest at
   `capture.manifest_path`).

If you find yourself writing capture code before step 4 closes, stop. The cost
of throwing away wrong capture code dwarfs the cost of one extra review round.

## What the manifest is not

- Not a test plan. It enumerates chapters and their UI flows for documentation
  purposes; it is not the project's e2e coverage matrix.
- Not a place to encode engine APIs. Selectors, wait strategies, storage state
  paths, container hostnames — none of those belong here. They live in the
  capture spec or in the profile's `capture.command`.
- Not a substitute for the live-action and PII rules in
  `references/capture-safety.md`. The manifest lists *what* you capture; the
  safety reference governs *what you must not* trigger while capturing.
- Not auto-generated. A script that scrapes routes and emits a manifest skips
  the entire point of human review. The manifest is something you draft by
  reading the running UI and then ask a human to bless.

## Shared-edit hotspots

Three artifacts are **append-hotspots** — every chapter effort grows them, so
parallel efforts on different chapters collide on the same lines:

- the capture manifest (every chapter appends its entry),
- the glossary index (every chapter appends its terms),
- the chapter index (every chapter appends its link).

When two efforts touch the same hotspot, resolve the conflict **additively** —
keep both contributions — and then **re-run the project's type/lint check**
before moving on. A blind "keep both" in a merge tool can fuse two manifest
objects into one malformed entry (a dropped comma, two values under one key, a
duplicated `slug`); the type/lint pass is what catches that the resolved file is
still valid, not just textually merged.

The durable fix is to **stop sharing the list at all**: split the manifest into
**per-chapter modules** — e.g. `manifest/<slug>.ts` (or `<slug>.yml`)
re-exported from a thin index — so each chapter effort edits only its own file
and the index changes by one import line, not a contested array body. The same
shape applies to the glossary and chapter indexes. Recommend this split to the
project the first time two chapter efforts collide on a shared list.

The engine-agnostic rules here are normative; any `*.playwright.*` asset shipped
under `../assets/` is a **non-normative reference implementation** for the
Playwright reference case — reimplement the driver glue for another engine; the
engine-neutral `../assets/lib/*.mjs` helpers are reused as-is.

## When the manifest changes

Any change to a manifest entry after capture has already run — new step, new
role, renamed glossary term, route change — goes through the same review loop.
The screenshot set is regenerated for the affected chapter only. The page
identity asserts in the capture spec are what make a stale capture fail loudly
instead of silently producing a wrong chapter.
