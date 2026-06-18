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
