# The AI-consumer axis

When the product's consumer is a model that reads a skill and its reference docs and then authors a deliverable, prose stops being documentation and becomes the program. That changes triage in both directions, and the two directions are easy to confuse because both look like "it's only a doc".

## Side A — a documented limitation is a real fix

If the reader can act on the sentence at authoring time, the sentence closes the defect. There is nothing left to build.

Acting on it means: choosing a different spelling, verifying by eye before proceeding, pinning a value the tool cannot infer, halting and asking. The reader has a full read of the file it is editing plus Read, Grep and directory listing — so anything expressible as "look at X and confirm Y" is already implemented the moment it is written down.

Consequences for triage:

- **Check for the shipped disclosure before scheduling any hardening.** A residual named accurately, in the place the reader looks, is not a promise to fix later. It is the fix.
- **A one-time sweep is prose.** "If this deliverable predates version N, read every embed once and rewrite the ones that do not resolve" is a complete answer where the consumer does that natively. A module that performs the same sweep is machinery with its own review surface.
- **A verification step is prose.** "Read the region around the row and confirm it sits under its container" beats a confirmation mechanism, because the reader can already see the file.

## Side B — an over-promise causes real damage

A human reader hedges an over-confident sentence against what they see. A model follows it literally, reports success, and moves on. So a false sentence is not cosmetic: it converts into a green run over a corrupt deliverable.

The damaging shape is specific: **the doc states a guarantee the reader is instructed to rely on, the code cannot deliver it, and every gate stays green.** The reader does exactly what it was told, the output is wrong in a way no check inspects, and the operator finds out from the published artifact.

Consequences for triage:

- Prose defects of this shape are `real`, not `doc-is-the-fix` — the disposition still lands on prose, but they get scheduled and verified rather than deferred to the next touch of the file.
- The strongest promotion signal is an instruction to *depend* on the false claim ("do not accept the default, pin it"). That converts a wrong sentence into a wrong action.
- Deleting the false clause is usually most of the fix. Adding the missing capability is the rest, and it is often one option on a call site — re-price it before assuming otherwise.

## Telling the sides apart

Ask three questions in this order.

1. **What does the reader do with this sentence at authoring time?**
   - Actionable and true → disclosure; the issue is closed by what already ships.
   - Actionable and false → `real`, and near the top of the batch.
   - Not actionable by the reader at all — it restates a code branch order for whoever next edits that code — → neither. No consumer of the product is harmed. Fix any sentence that is false and file nothing.
2. **Is the induced failure loud or silent?** A wrong sentence that produces a halt is survivable. A wrong sentence that produces a green run over a wrong artifact is the class this repo cares about most.
3. **Does the reader already have the capability the issue wants built?** If yes, the issue is prose. If no — the wrong outcome happens inside a function the reader never inspects, and the reader is told the run succeeded — prose cannot reach it and the code must change.

## The distinction that decides the hard cases

**Prose the reader executes is control flow. Prose the reader merely reads is a note.**

A contract step in an adapter document — "before acting on this outcome, check X and halt naming both spellings" — changes what the deliverable becomes, exactly as a branch would. That is why a defect in a shipped function can legitimately be fixed by a bullet in a contract document rather than by a new return value: the contract is the layer the consumer runs.

The inverse trap: **"the model will figure it out" is not a fix.** Prose fixes what the reader decides. It does not fix what a function returns behind the reader's back. When the branch is wrong inside code the reader never sees, and the reader is handed a success, no sentence anywhere reaches it.
