# From finding to tracker issue

A review round ends in dispositions. Only some of them are entitled to a tracker number. This reference covers that boundary, and the triage of a backlog where the boundary was never enforced.

The gate: name the shipped function whose branch is wrong, and a person who is not a reviewer who reaches it. One wrong branch is one issue. If the function is a doc sentence, the fix is that sentence.

## Four mechanisms that manufacture issues

### Input-spelling enumeration

*Tell.* Two or more open issues cite the same function and differ only in the input that reaches it — a link, a decorated label, an invisible character, a newline. The titles look distinct; the wrong branch is one branch. The prose variant: consecutive review rounds each filing a different paragraph of one file.

*Counter-move.* File per wrong branch, never per input. Later spellings become measured rows on the first issue. Duplicates are not merely noise: split across numbers they attract opposite remedies — one widens the match, the other fails closed — so whichever ships first makes its twin wrong, and the twin is still open to be implemented.

### Symmetry standing in for a scenario

*Tell.* The justification compares two branches — "the sibling branch refuses this, so this one must too" — and no sentence names who produces the input.

*Counter-move.* Symmetry is an argument about code shape. It establishes consistency; it never establishes a producer. Require a real authoring step, export, or user action. If the only producer is a hand-built fixture, the finding is a test, not an issue. Symmetry can still earn a comment or a note applied at the next touch of the file.

### Round-N residual dumping

*Tell.* A cluster of issues filed minutes apart while closing a release. An issue whose own text says it audits a contract in a file that is not in the diff. A finding that exists because the loop was obliged to produce one more round.

*Counter-move.* The cap exists so a loop can end without residue. A late round with nothing in scope is the finish signal, not permission to widen the artifact. Findings from a widened scope meet the same two names as any other — a late or out-of-scope finding gets a higher bar, not a lower one, because out of scope is where unreachable issues come from.

### The reviewer's remedy becomes the spec

*Tell.* The body of the issue is an implementation — a module that owns the filesystem, a staged-commit transaction with rollback, another return value threaded through every adapter — and the defect it serves is one paragraph above it.

*Counter-move.* Separate defect from remedy; the remedy is a second untrusted claim, and new machinery is new review surface. Size it against the actual consumer. When the consumer is a model reading a skill, one accurate sentence of prose usually delivers what the proposed machinery would. File the defect, record the remedy as one option, and say what the sentence would have cost.

## Triaging a backlog that was never gated

Sorting only. The defended verdict on a number that is already open — the dispositions, the brakes, and the refutation second opinion every close needs — belongs to `skill:tracker-triage`.

1. Group every issue by the shipped function it blames. An issue that cannot name one is not a defect report.
2. Within a group keep the oldest number and fold the rest in as rows, and state which remedy the group settles on before closing the others.
3. Sort the ungrouped remainder: a named non-reviewer consumer keeps it open; a correction that is one sentence of prose gets edited at the next touch and closed; no consumer means park or close — say which, in the issue, or the next sweep refiles it.
4. Re-verify against current code before scheduling. An issue outlives the code it describes, and an already-fixed defect reads exactly like a live one.

The gate is a bar, not a quota. Judge each issue on its own evidence and find the severe ones first, so a sweep that closes most of a tracker cannot swallow one.

## Measured outcome

One tracker, 30 open issues, audited issue by issue against the gate:

- 6 are work worth scheduling;
- 8 duplicate a defect already filed — 3 of them the same wrong branch under a different input spelling;
- 1 was already fixed in shipped code;
- 6 more are a correction that is one sentence of prose and needs no number (7 with the already-fixed one);
- 9 are wishes with no named consumer.

Applied at filing time, the gate yields 6 numbers plus about 7 prose edits: roughly 13 items of substance where 30 tickets stood. All four mechanisms are represented among the 24 that did not survive as scheduled work.
