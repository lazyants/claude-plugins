# Diátaxis — choosing the right shape for each chapter

Diátaxis is a framework for organizing user documentation into four distinct kinds,
each serving a different reader need. You use it to decide what shape a chapter
should take *before* writing — the wrong shape produces docs that frustrate the
reader even when every fact is correct.

The four quadrants are split along two axes: **learning vs. doing** (what is the
reader trying to accomplish right now?) and **practical vs. theoretical** (do they
need steps or understanding?).

| | Learning | Doing |
|---|---|---|
| **Practical** | Tutorial | How-to guide |
| **Theoretical** | Explanation | Reference |

## The four quadrants

### Tutorials — learning-oriented
A tutorial is a guided lesson for a newcomer. The reader has no goal beyond
"learn the product"; you supply the goal. Tutorials are linear, opinionated, and
choose one happy path through the product. They prioritize the reader's first
success over completeness — every detour, edge case, or "you could also" is a
distraction. A tutorial ends with the reader having *done* something concrete
and gained confidence to try more on their own.

### How-to guides — task-oriented
A how-to guide answers "how do I accomplish X?" for a reader who already knows
what they need. The reader brings the goal; you supply the steps. How-to guides
are recipes: terse, focused, complete for the one task, and they assume the
reader can fill in surrounding context. They are the workhorse of an end-user
handbook — most chapters in a feature-oriented product handbook are how-to
guides.

### Reference — information-oriented
Reference describes the machinery: every field on a form, every option in a
dropdown, every status code, every permission flag. Reference is consulted, not
read. It is exhaustive, neutrally ordered (alphabetical, by screen, by entity),
and ruthlessly factual. It does not teach and it does not guide — it answers
"what does this thing do / mean?" for a reader who already knows where to look.

### Explanation — understanding-oriented
Explanation discusses background, rationale, and concepts. It answers "why does
the system work this way?" or "how should I think about X?". Explanation is
discursive: it gives history, trade-offs, and mental models. End-user handbooks
use explanation sparingly — usually one short chapter that frames a domain
concept the reader must internalize before the how-tos make sense.

## How the skill picks quadrants per project

You do not write all four quadrants for every project. The profile field
`diataxis.quadrants_in_use` is a subset of
`[tutorials, howtos, reference, explanation]` declaring which shapes this
project's handbook ships. When you plan a chapter:

1. Read `diataxis.quadrants_in_use` from the profile.
2. Decide which quadrant the chapter belongs to based on the reader need it
   serves (see "When this is the right shape" below).
3. If the chosen quadrant is **not** in `quadrants_in_use`, either re-shape the
   chapter to fit a quadrant that *is* declared, or halt and ask the user
   whether to add the new quadrant to the profile. Do not silently produce a
   shape the profile excluded.
4. Apply the discipline of that quadrant for the whole chapter — do not mix
   shapes within one chapter. A how-to guide that drifts into explanation
   loses its task-recipe value.

A typical end-user handbook declares `[tutorials, howtos]` — onboarding plus a
catalog of task recipes — and uses reference and explanation only when the
domain genuinely demands them.

## When each quadrant is the right shape

The paragraphs below apply only if the quadrant is listed in
`diataxis.quadrants_in_use`. Skip the ones that are not.

### When **tutorials** is the right shape
A tutorial is right when the reader is brand-new and needs a guided first
experience that ends in a concrete win. Pick this shape for an onboarding
chapter ("getting started", "your first <thing>") or for a self-contained
guided lesson on a feature whose value only becomes obvious after seeing it
in action. The chapter walks the reader through a fixed scenario you chose;
it does not branch, it does not enumerate options, and it does not pause to
explain alternatives. The success criterion is "the reader finishes with a
working result and the confidence to explore". If you cannot describe the
finishing state in one sentence, the chapter is probably a how-to or
reference, not a tutorial.

### When **howtos** is the right shape
A how-to is right when the reader already has a specific goal — filter a
list, configure a setting, export a report, invite a colleague — and just
needs the steps. Pick this shape for the bulk of feature chapters in an
end-user handbook. Each how-to covers exactly one task, starts from a
clearly stated precondition, lists numbered steps in the imperative, and
ends when the task is done. Do not pad with background; do not branch into
"if you want X instead". One how-to, one task. Variants get their own
how-to chapter or their own clearly labeled subsection.

### When **reference** is the right shape
Reference is right when the reader needs to look up a fact about the
system — what a field accepts, what a status means, what a permission
controls, what an error code indicates. Pick this shape for catalogs
(field-by-field form documentation, status-code tables, role-permission
matrices, glossary-adjacent term lists that need more than a glossary
entry). Reference is ordered for lookup (alphabetical, by screen, by
entity), not for reading top-to-bottom. It is complete and neutral: every
field gets the same treatment, no editorializing, no tutorials hidden
inside.

### When **explanation** is the right shape
Explanation is right when the reader needs a mental model before the
how-tos can land — a domain concept, a workflow shape, a rationale for why
the system is partitioned the way it is. Pick this shape sparingly, and
only for concepts the reader genuinely cannot work without. The chapter
discusses, compares, gives history or rationale; it does not list steps
and it does not enumerate fields. One short explanation chapter at the
front of a section is usually enough — if you are writing your third
explanation chapter, ask whether the concepts could ride along inside the
relevant how-tos instead.
