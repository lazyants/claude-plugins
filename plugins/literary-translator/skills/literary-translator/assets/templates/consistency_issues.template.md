<!--
  consistency_issues.template.md -- one-time-seed skeleton for the W6
  cross-segment consistency tracker.

  Step 0a copies this file ONCE to `${durable_root}/consistency_issues.md`
  and never re-copies over it once it exists (see SKILL.md's Step 0a).
  Unlike `style_bible.md`/`PLAN.md`, this file ships with no
  `LT_REQUIRED_FILL_BEGIN`/`END` marker spans at all -- nothing about a
  real cross-segment inconsistency is knowable before the first batch has
  actually been translated and reviewed, so there is nothing genuinely
  fillable at scaffold time. `scripts/scaffold_validate.py`'s own
  docstring explicitly allows this: a `MARKER_SCAN_FILES` entry with zero
  marker spans simply passes the W1 gate with nothing to find.

  This file is hand-maintained only, per SKILL.md's W6 Consistency pass:
  "cross-segment sweep using consistency_issues.md as a lightweight,
  hand-maintained tracker after every batch, before the next starts. Never
  the output of an automated script, never read back in or acted on
  programmatically." Nothing in this codebase parses this file's
  content -- it exists purely so the humans (and Claude, as curator)
  running this project can track and eventually resolve cross-segment
  issues that per-segment review cannot catch on its own, since each
  reviewer call only ever sees one segment in isolation.
-->

# Consistency-pass tracker (W6) -- [PROJECT TITLE / AUTHOR / PERIOD -- fill in]

Cross-segment inconsistencies that per-segment review cannot catch (each reviewer sees one segment in
isolation). Swept in a deterministic pass after each batch, before the next batch starts, and again as
part of the W7 final audit. Each item below should record: what was found, the decision, and the exact
normalization to apply across every affected segment (if any) -- or, when the honest answer is "leave it
as genuinely ambiguous," record that decision explicitly too, rather than silently resolving it.

Typical sources for an item: `final_audit.py`'s WARN-only checks (glossary-diff, link-graph,
foreign-remainder scan, verse-structure -- see `references/assembly-and-output.md`), a reviewer flagging
the same drift independently in two different segments, or a `canon.json` `review_queue[]` entry that
turns out to affect more than one already-converged segment.

## Open

_(none yet -- append one bullet per unresolved item as the first batch's review surfaces one)_

## Resolved

_(none yet -- append one entry per closed item: what was found, the decision, the exact normalization
applied, and which segments were actually touched)_
