---
name: tracker-triage
description: Decide whether a tracker issue is real work or an artifact a review session manufactured, and defend the verdict either way. Run it BEFORE FILING a new issue — these tests ARE the filing gate — and when triaging or auditing an existing backlog, when a tracker looks inflated by review rounds, when choosing between schedule, fold, park, close and prose-fix on an issue, when several open numbers may describe one defect, when an issue's premise may be stale, or when an issue proposes machinery whose only consumer is a model reading a skill.
---

# Tracker triage

Given an issue — open, or about to be filed — is this real work, or did a review session manufacture it?

There are two failures here and they are symmetric. A manufactured issue kept open taxes every planning pass that follows it. A real defect closed because a hostile reader could not picture its user ships silent corruption of a deliverable. Run this to produce a defended verdict, never to shrink a tracker.

**These tests run at FILING time, not only after a number exists.** The gate a finding must pass to earn a number is this same list, applied while the issue is being WRITTEN — triaging your own number at the next sweep is rework, and an untriaged number taxes every planning pass in between. `skill:review-loop-discipline` still owns the surrounding policy: who may file, where, and the mechanisms that manufacture issues in the first place.

## The two names

Every verdict rests on the same two answers, recorded verbatim in the triage:

1. the shipped **function** whose branch is wrong — opened in current source, never recalled; and
2. a **person who is not a reviewer** who reaches that branch doing their own work.

Neither is optional; neither alone is sufficient. "Which function" answering with a doc sentence is a real answer that moves the disposition, not a failed test.

## Tests, in order

Run until one is decisive. Record which one decided, and against which source revision.

1. **Provenance read.** Read what the body says about its own origin before you read its claim. Round number, split-from lineage, a filing timestamp inside a release burst, a justification made of symmetry. Provenance sets the prior and never the verdict — a batch where nearly every issue is review-discovered still contains its real defects. → [provenance-reads.md](references/provenance-reads.md)
2. **Producer test.** Name who authors the triggering input and where they author it. A role and a path, not a hypothetical. "Only a test produces it" is decisive against. Before closing on a crafted string, try to widen it to an ordinary one yourself — attack the example, not the issue. → [triage-tests.md](references/triage-tests.md)
3. **Duplicate test.** Key on the function and its branch, never on the title or the input. One broken branch is one ISSUE, however many inputs reach it; later input spellings are measured rows on the survivor, never new numbers. The survivor is the number carrying the widest measured evidence — not the oldest, not the loudest. Two open numbers on one branch attract opposite remedies, and that — not the noise — is the damage.
4. **Over-claim test.** If the issue exists because a shipped sentence promises what the code does not do, the default fix is deleting the promise, not building the behaviour. A residual already disclosed accurately owes nothing further.
5. **Trust-boundary test.** Write down who owns the app, the machine, the output directory and the record. An issue defending against a party inside that boundary is machinery. Data crossing outward — a customer's row composited into a published screenshot — is the inverse case and outranks everything else in the tracker.
6. **AI-consumer test.** The reader executes the prose. A limitation the reader can act on at authoring time is closed by the sentence that states it. A promise the code cannot keep is a real defect whose failure mode is a green run over a corrupt deliverable. → [ai-consumer-axis.md](references/ai-consumer-axis.md)

Then, before any verdict lands:

**Re-price the fix yourself.** The issue's acceptance criteria are the reviewer's imagined remedy, not the defect. Read the consequence, derive the smallest change that removes it, judge that price. Re-pricing promotes as often as it demotes: an issue filed as a new module can be two sentences and schedulable today; an issue filed as a one-liner can be the highest stake in the tracker.

## Dispositions

Return exactly one per issue. Each obliges something in the same pass.

| Disposition | Obliges |
| --- | --- |
| `real` | Write the two names and your priced fix into the body, and say explicitly which proposed remedy you are *not* taking. |
| `fold` | Close into one survivor — the number carrying the widest measured evidence. Carry each closed issue's one unique nuance forward as a comment. State which remedy the group settles on. |
| `doc-is-the-fix` | Name the file and the sentence. Edit at the next touch of that file; no number. If the sentence is a promise a reader acts on, this is `real` instead. |
| `park` | Say in the body what would unpark it — a named consumer, not a date. Otherwise the next sweep refiles it as new work. |
| `close` | Run the refutation second opinion first. Leave a one-line reason naming the test that decided. |
| `already-fixed` | Close citing the commit and the line that fixed it, read in current source. |

`premise-stale` is a rider, not a disposition: the body is measurably wrong while the defect may be real. Correct the body **before** any other disposition lands — an implementer reads the body, never the triage.

Never close A into B and B into A — and never close A into B when B is closing too. The transitive case is the one a multi-pass triage produces, and it is caught by asserting over the survivor set before anything is written, never by reading. Never close an issue a shipped doc cites by number; park it with a note instead.

## Brakes

- **Count, do not impress.** "The tracker is inflated" is the hypothesis; the tally by disposition is the finding. Produce it before the ruling and report it with the ruling.
- **A hostile prior is not a quota.** There is no target close rate. If most of the backlog survives the tests, that is the answer.
- **Reproduction is not the triage axis, in either direction.** Most of a manufactured batch reproduces exactly as written. And a failed reproduction from a wrong path or a stale tree is not a refutation.
- **Spot-check every `close` whose failure mode is silent.** Loud failures self-correct. Ask: if this verdict is wrong, does anyone find out? If not, verify the mechanism in current source yourself before closing.
- **Every `close` gets an adversarial second opinion whose job is to refute it**, not to review it. → [brakes.md](references/brakes.md)
- **Record what you did not do.** The parked set and its unpark conditions are part of the deliverable.

## Reference routing

- [provenance-reads.md](references/provenance-reads.md) — reading a body's own language and metadata, and the limits of what provenance proves.
- [triage-tests.md](references/triage-tests.md) — running the producer, duplicate, over-claim, trust-boundary and re-pricing tests, with the traps each one has.
- [ai-consumer-axis.md](references/ai-consumer-axis.md) — when the consumer is a model reading a skill: which side of the axis an issue lands on, and why prose is load-bearing here.
- [brakes.md](references/brakes.md) — the tally, the silent-failure spot check, and the refutation brief.
- [enduser-handbook-batch.md](references/enduser-handbook-batch.md) — the worked batch these rules came from: counts, the issues that survived a hostile read, and the two verdicts that were overruled.
