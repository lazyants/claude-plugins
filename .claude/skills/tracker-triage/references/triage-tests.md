# Running the tests

Each test below can decide an issue on its own. Run them in the order given in SKILL.md and stop at the first decisive answer, but record the answer you got — a `close` defended by "the producer is a fixture" is auditable; a `close` defended by "this felt invented" is not.

## Producer test

**Question.** Who authors the input that reaches the wrong branch, and in which file do they author it? Answer with a role and a path.

Decisive **against** the issue:

- The only producer is a test or a crafted string — a padded 400-character label, an injection sentence, a delimiter collision, a value that exists to break a parser.
- The input has to be *deliberately* spelled: a quoted escape, a block scalar, an encoding nobody types. If the plausible version of the same input is already handled, the deliberate version is not a defect.
- The producer is the tool itself, writing a value it will read back — then the trigger is unreachable unless the tool is already broken elsewhere.
- The author is the reviewer. An input that exists because the review needed one is not an input.

Decisive **for** the issue:

- The input is ordinary curation of a file the operator owns — a decorated section heading in a hand-kept index is unremarkable authoring, not an attack.
- The input ships in the repo's own example, the one artifact every adopter copies.
- The input is what a normal deployment produces without anyone choosing it: a route substring, a third-party asset host, a locale negotiated from a header.

Traps:

- **Attack the example, not the issue.** A hostile-looking trigger can be one spelling of an ordinary one. Try to widen it yourself before closing — if you can reach the same branch with a title, a route, or a label a real author would write, the producer test passes even though the issue's own example fails it.
- **Symmetry is not a producer.** "The neighbouring branch refuses this" is an argument about code shape. It buys consistency for the next maintainer of the code, which is a note at most.
- **Name the conjunction.** Some issues need two fields with *different provenances* to disagree — one pasted from a web UI, one typed clean. That conjunction, not either half, is what needs an author. State it explicitly; it is usually much narrower than the body reads.

## Duplicate test

**Question.** Does an open issue already name this function and this branch?

- Group the whole backlog by the function it blames before judging any single issue. An issue that cannot name one is not a defect report.
- One wrong branch is one issue however many inputs reach it. Later inputs are **measured rows on the survivor**, never numbers.
- The survivor is the number carrying the widest measured evidence, not the oldest and not the loudest.
- **Divergent remedies are the real damage.** Split across numbers, duplicates attract opposite fixes — one widens the match, the other fails closed. Whichever ships first makes its twin wrong, and the twin is still open for someone to implement. Settle the remedy for the group in the survivor's body *before* closing the others.
- Carry forward exactly one thing from each closed duplicate: the nuance the survivor lacks. Discard the rest.

## Over-claim test

**Question.** Does this issue exist because a shipped sentence promises behaviour the code does not have?

- Two candidate fixes always exist: build the behaviour, or delete the promise. **Default to deleting the promise.** Building it needs the producer test to pass on the promise itself — a named consumer who needs that behaviour, not a reader who was merely told it existed.
- Check the shipped disclosure before scheduling any hardening. A residual documented accurately, in the place the reader looks, is a *shipped fix*. Nothing is over-promised, so nothing is owed.
- Split the direction: the code is honest and the doc lied → prose. The doc is honest and the code silently mis-serves → code.
- A doc that restates a code branch order for the next maintainer of that code is neither. Nobody consuming the product is harmed by prose being repetitive. File nothing; fix any sentence that is actually false and stop.

## Trust-boundary test

**Question.** Who owns the app, the machine, the output directory, and the record — and does this issue defend against someone inside that list?

- Write the ownership list down once per repo and reuse it. Where the operator owns all four and is the record's only consumer, an issue about a hostile label, a malicious index file, or an unsanitised string the operator wrote themselves is machinery. Sanitising input for a reader that has already read the whole file buys nothing.
- The inverse is the highest-stake class in the tracker: data crossing **outward**, out of a boundary the operator does not own — a customer's record, live integration data, anything composited into an artifact that gets published. Those outrank everything.
- Prefer failing loudly to defending. Inside the boundary, a guard that refuses an unknown is cheap and honest; a sanitiser that tries to render hostile input safe is a module.
- Ask which direction the asymmetry runs. A design where the *forgetful* mistake is silent and the *explicit* mistake is loud is backwards, and fixing that asymmetry is usually a handful of lines — that is a real issue even inside the boundary.

## Re-pricing

**Question.** What is the smallest change that removes the consequence?

Derive it yourself, from the consequence, before reading the issue's acceptance criteria. Then compare.

- Tells that you are reading a remedy and not a defect: a new module that owns the filesystem, a transaction with rollback, an extra return value threaded through every adapter, five acceptance criteria, a plan revision history.
- Re-pricing **promotes**: an issue filed as a large feature can be two false clauses to delete and one option to pass, which is schedulable today.
- Re-pricing **demotes**: an issue whose only defensible residue is half a sentence is a prose edit, not a ticket.
- The price of the *right* fix decides. Never let the price the issue asked for decide, in either direction.
- When you schedule, say in the body which proposed remedy you are not taking, and why. Otherwise the implementer builds the machinery the tracker recorded.
