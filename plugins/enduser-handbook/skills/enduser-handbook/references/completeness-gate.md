# Completeness gate — the coverage-matrix audit

You build a coverage matrix before you publish. It is the only reliable way to
catch a silently-omitted function. A feel-based read-through misses exactly the
read-only icons and live-action surfaces that look skippable — those are the
omissions reviewers and end-users notice first. If the matrix is missing or has
an un-resolved row, you do not publish.

## What the matrix is

A table indexed by **interactive trigger** (page-root and every
`components/<feature>/**` child of the surface you're documenting) with three
columns:

| Trigger (verbatim UI label) | Side-effect class | Status |
|---|---|---|
| … | read-only \| mutating-reversible \| live-external \| irreversible-send | documented \| disclosed |

You derive the trigger list by walking the running-UI source (see
`running-ui-source.md`) — the page entry from `stack.frontend.page_globs` and
every component it pulls in under its feature subtree. Buttons, menu items,
icon-only controls, status badges that double as links, file-download anchors,
`mailto:` links, modal-open affordances. If a user can click it or read it as a
status, it is a row.

## Side-effect classification

Each trigger falls into exactly one of four classes. Same taxonomy as
`capture-safety.md`; that file owns the safety rules, this file owns the audit.

- **read-only** — display, status icon, list render, file download, `mailto:`,
  modal that opens a view and exits with no write. Includes per-card symbols
  and status/download icon rows.
- **mutating-reversible** — writes you can undo through the UI (toggle a flag,
  edit a label, delete a draft).
- **live-external** — fires a request to an external system the project flags
  as live (see `capture.live_action_examples` in the profile — the project
  curates the concrete examples; the principle binds even when that list is
  empty).
- **irreversible-send** — cannot be undone through the UI (final submit,
  permanent delete, signature send).

Classify from the running-UI source plus the project's
`capture.live_action_examples`. Do not guess — if you cannot tell, ask before
you publish; an unclassified row is a blocking defect.

## What "documented" means

The trigger has a real captured screenshot embedded in the chapter and prose
that names the verbatim UI label. For read-only items, the captured state is
the rendered surface (an icon legend counts — a symbol-reference table is the
canonical way to cover an icon row). For live-external and irreversible-send
items, the captured state is the **read-only open state** only (the modal as it
appears before the user clicks send/submit/delete). Capturing past that point
is a `capture-safety.md` violation; it does not earn a "documented" mark, it
earns a halt.

## What "disclosed" means

The chapter prose explicitly tells the reader the function exists and why it is
not shown in full. One sentence is enough — the bar is that a reader who scans
the chapter knows the function is there and is not surprised when they
encounter it in the app. Acceptable shapes:

- "The page also offers an X action; it is not shown here because it fires a
  live request to <external system> and we do not trigger it in the handbook."
- "An X control appears in the row menu for administrators with the Y
  permission; the role used for this chapter does not have it."
- "X is a read-only status indicator and is described in the symbol legend
  below."

Not acceptable as disclosure: a manifest comment, a TODO, a commit message, an
adjacent chapter, a glossary entry alone. The disclosure has to live in the
prose the end-user actually reads.

A trigger that is neither documented nor disclosed is a **defect**. "Out of
scope" only counts when it is written for the reader. Silence is not scope.

## Disclosure prose templates

When a row is disclosed rather than documented, do not invent the sentence from
scratch each time — fill in one of these. Each is one sentence; replace every
`{…}` with the project's real label and render it in the profile's
`language.register`.

- **Live-external send:** "The page also offers a **{action}** control; it is
  not shown here because it dispatches a live request to **{external system}**,
  which we do not trigger in the handbook."
- **Permission-gated:** "A **{action}** control appears for **{role/permission}**;
  the **{role}** used for this chapter does not have it."
- **Read-only indicator:** "**{element}** is a read-only **{status/indicator}**
  and is described in the symbol legend."
- **Error-state on a missing prerequisite:** "The **{feature}** requires
  **{prerequisite}** that is absent in the handbook environment, so it returns
  an error state and is described rather than shown."

## Disclose TRIGGER LIST

Disclose-don't-capture is a mechanical call, not a judgement: disclose (do not
capture) when **any** of the following holds.

1. The target errors / 500s because a required file, record, or integration is
   absent in the seeded environment.
2. The control renders or sends a LIVE document or request to an external
   system.
3. Capturing it needs real (un-maskable) PII.
4. The action is irreversible / destructive past a read-only open state.
5. The control is gated to a role this chapter does not use.

If none of these holds, the row is capturable — document it. If one does, write
a disclosure sentence from the templates above.

## Per-page state-coverage checklist

Trigger coverage is one axis; the **page state** a trigger was captured in is another. For each
page in the matrix, track state coverage separately:

| State | Status |
|---|---|
| populated | documented — required; every page must reach this |
| empty | documented \| disclosed \| n/a |
| error | documented \| disclosed \| n/a |
| denied | documented \| disclosed \| n/a |

**populated** is always required — the happy-state capture is never optional. **empty / error /
denied** are opt-in: capture them only where the variant adds documentation value, and mark each
either `documented` (a real captured variant, per `references/state-variants.md`), `disclosed`
(prose names the variant and why it is not shown), or `n/a` (the page genuinely has no such state —
e.g. a list that can never legitimately be empty). Do not leave a row blank; a blank row is the same
un-resolved-row defect as an unclassified trigger. See `references/state-variants.md` for how each
variant is captured as a real app state and the `state` marker that anchors identity on it.

## The audit walk-through

Run this before every publish, not only on first authoring:

1. **Enumerate.** From `stack.frontend.page_globs` (and the feature's
   component subtree), list every interactive trigger with its verbatim label.
   Cross-check against the capture manifest entry (`manifest-discipline.md`)
   — every manifest step should map to a row; rows missing from the manifest
   are the suspect set.
2. **Classify.** Assign each row a side-effect class. Use
   `capture.live_action_examples` as the project-specific signal for the
   live-external class; default-to-cautious when ambiguous.
3. **Status check.** For each row, mark documented or disclosed by reading the
   chapter and the captured assets — not by reading your own intent.
4. **Diff.** Compare the row set against the chapter's screenshot embeds and
   prose mentions. Any trigger present in the UI source but absent from both
   the embeds and the prose is a missing row.

A short script that enumerates triggers from the source globs and prints the
matrix template is reasonable project tooling — it does not replace the
classify/status pass, which is a reading task.

## Surface enumeration (mechanical first pass)

Before the reading pass, do a mechanical first pass that enumerates the
interactive surface so the matrix starts from observed controls, not from
memory. Enumerate the **live DOM** — the controls actually rendered in the
running UI — **not** the framework route/page source (a Blade/Vue/React page
file can declare controls behind unmet `v-if`/role gates that never render, and
miss ones injected at runtime). For each control capture, verbatim:

- the **text** (the displayed label),
- the **title** attribute,
- the **aria-label**,
- the **href**,
- the **role**.

**Include icon-only controls** — controls with no visible text. And **never
filter the enumeration by text presence.** A `text || href`-style filter
silently drops icon-only destructive controls (a delete/trash/tag glyph with an
`aria-label` but no text node) — and an icon-only destructive control is exactly
the row that gets missed, which is the failure this gate exists to catch (a
short control count produces a fabricated coverage matrix). Keep every record;
let the classify pass decide, not the enumerator.

**Framework-styled controls are enumerated, but not "genuine".** The audit now
also matches `.btn` / `[data-bs-toggle]` / `[data-toggle]` controls so a glyph
styled as a Bootstrap button (`<span class="btn glyphicon-trash">`) is no longer
invisible. But a control matched **only** by one of these class/attribute hooks
is enumerated, not a "genuine control" — so its visible **text** is SUPPRESSED
(a `<span class="btn">Jane Doe</span>` clickable name must not leak its label).
Read its label from `aria-label`, `title`, or the human scrub, exactly as you
would for an icon-only control. A real button (`<button class="btn">` /
`<a class="btn" href>`) is genuine on its own tag/href and keeps its label as
before.

This guidance is the normative, engine-agnostic rule;
`../assets/surface-audit.playwright.ts` is a **non-normative reference implementation**
for the Playwright reference case — reimplement the driver glue for another engine; the
engine-neutral `../assets/lib/*.mjs` helpers are reused as-is.

**PII boundary of the mechanical pass.** The enumeration captures control
**identity** verbatim — `aria-label`, `title`, `name`, `href` (including
`mailto:` addresses), `className` (`class`), and a genuine control's visible
**text label** — because those *are* the label the matrix needs. `className` is
developer-authored (utility / framework / icon classes) and is captured for the
destructive-control classification; treat it as a verbatim field under this same
boundary, and scrub it in the human pass if an app encodes record/user slugs
into class names (`class="customer-row-jane-doe"`). A reference implementation can and should
suppress text that is **not** a control label: a value-bearing control's user
data (a `<textarea>`/`<select>`/contenteditable's content, an input's prefilled
value) **and** the aggregate text of a non-control element matched only by a
broad identity attribute (a `<div data-testid="customer-details">…</div>` data
region or a row container) — that aggregate text is page data, not a label, so it
is dropped. But it **cannot** strip PII that lives inside an **identity label it
must keep**: a genuine control's own visible label (a clickable customer name, an
`aria-label="Delete order for jane@example.com"`), and — because an icon-only
control (`<span aria-label="Delete">`) is indistinguishable from a labelled data
region (`<div aria-label="Jane jane@example.com">`) — the `aria-label`/`title` of
**every** matched element is retained, so those can carry region PII too. No
pattern mask can detect a bare name. So treat this pass as a **first pass, not a
PII guarantee**: run it
**only against seeded / non-PII data** (`capture-safety.md` seed hermeticity),
and **scrub any residual PII from the matrix labels in the human classify pass
before committing**. The enumerator is non-authoritative; the classify pass is
what ships.

While you are here, cross-check the two glossary lists for sync: the capture
manifest's `glossary_terms` list (see `manifest-discipline.md`) and the chapter
frontmatter's `glossary_terms` list are two separate lists that MUST match. A
term in one but not the other is a drift defect — resolve it before publish.

## Block-vs-allow rules for publish

The audit **blocks** publish when any of the following hold:

- A trigger is unclassified.
- A trigger is neither documented nor disclosed.
- A trigger is marked documented but the referenced screenshot file does not
  exist on disk.
- A live-external or irreversible-send trigger is marked documented past its
  read-only open state.
- A row was added to the manifest but the corresponding capture spec did not
  run (no asset under the chapter's asset dir).

The audit **allows** publish when every row is classified, every row is either
documented (with a real asset) or disclosed (with a real sentence in the
chapter prose), and the capture-safety constraints are intact. "Allow" is a
positive signal — a clean matrix is a publish-gate pass; an absent matrix is
not.

When you block, you do not silently fix and republish. You report the missing
rows to the user with their verbatim labels and the reason (unclassified,
undisclosed, missing asset, safety violation) and ask which path to take —
extend the seed/role to make the function capturable, write disclosure prose,
or drop the function from this chapter's scope. The decision is theirs; the
defect surface is yours.
