# Provenance reads

An issue body is written by whoever filed it, and a review session writes down more than it means to. Read the origin before the claim: it tells you which tests to run hardest and how much of the body to trust. It never tells you the verdict.

## What the body says about itself

Search the body — and the title — for these before anything else.

| Phrase in the body | What it establishes | What it does not establish |
| --- | --- | --- |
| "found during the verification round", "round N of an N-round review" | The finding arrived after the artifact was already judged sound. Late findings come from a widened scope. | That it is wrong. A late round can find the worst defect in the batch. |
| "split out of #NNN", "deferred from #NNN", "narrowed from #NNN" | The parent is the real unit. Run the duplicate test against the parent's function before reading further. | That the split was wrong — a genuinely different branch deserves the split. |
| "the sibling branch refuses this, so this one must too", "for symmetry", "a lone survivor reads to the next maintainer as an exception" | An argument about code shape. | A producer. No sentence here names anyone who authors the input. |
| "this file was not in the diff", "a contract audit of X" | The finding is out of scope of the change that produced it. | That the contract is fine. Out of scope raises the bar; it does not settle it. |
| Five or more acceptance criteria; a proposed module, transaction, rollback, or extra return value | The reviewer's remedy, written where the defect belongs. | The defect's size. Re-price it. |
| A measurement with no method ("this costs a rebuild", "nothing else can catch it") | Nothing. Attack it — a cost claim inside an issue is the least-audited sentence in the tracker. | |

## What the metadata says

- **Filing timestamps.** Issues filed seconds apart by one sweep are one finding under several titles until proven otherwise. A cluster filed late in the day that a release closed is residual dumping — the loop had to produce another round and went looking outside the artifact.
- **Author and lineage.** A number opened by the review session that also opened its neighbours, on the same branch of the same function, is a spelling variant. Group by function first, timestamp second, title never.
- **Age against the code.** An issue outlives the code it describes. A body describing a mechanism that shipped a rewrite ago reads exactly like a live one; that is `premise-stale`, and the correction comes before the disposition.

## What provenance cannot decide

Review-discovered is the normal case, not an accusation. In the batch these rules came from, nearly every open issue was review-discovered — **including every one that survived as real work**. A skill that closes issues on provenance alone would have thrown away the customer-PII leak and the broken example everyone copies.

So:

- Provenance chooses which tests to run hardest and how much of the body to trust.
- The **producer test** decides. An issue with the worst provenance in the tracker and a real ordinary author is real work.
- An issue with impeccable provenance and no author outside a fixture is not.

## The four manufacturing mechanisms

`skill:review-loop-discipline` carries these as filing-time policy with their counter-moves. At triage time you only need their tells, to know which test is load-bearing:

| Mechanism | Tell in the tracker | Load-bearing test |
| --- | --- | --- |
| Input-spelling enumeration | Several open numbers cite one function and differ only in the input string | Duplicate |
| Symmetry standing in for a scenario | The justification compares two branches and names no author | Producer |
| Round-N residual dumping | A timestamp cluster closing a release; a file in no diff | Producer, then trust boundary |
| The reviewer's remedy became the spec | The body is an implementation and the defect is one paragraph of it | Re-pricing |

Docs manufacture the same way: three consecutive rounds each finding a different paragraph of one file are one prose fix, not three numbers.
