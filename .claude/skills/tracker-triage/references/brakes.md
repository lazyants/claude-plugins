# Brakes

A triage pass runs with a hostile prior, and a hostile prior is comfortable: closing is cheap, quiet, and looks like diligence. These brakes exist because nothing downstream catches a wrong `close`.

## Count before you conclude

"The tracker is inflated" is a hypothesis. The finding is the tally.

Produce, before the ruling: total issues, count per disposition, and the count of distinct **functions** blamed. The last one is the number that exposes spelling enumeration — a backlog whose issue count far exceeds its function count is duplicated, and a backlog where they are close is not.

Report the tally with the ruling, and report the inverse count too: how many survived. A ruling that gives only the close rate reads as a purge.

## A hostile prior is not a quota

There is no target close rate. Judge each issue on its own evidence. If most of a backlog survives the tests, that is the answer and it is a good one.

The corollary matters more: **find the severe ones first**. Run the trust-boundary test's outward-crossing check across the whole backlog before triaging anything, so a sweep that closes most of a tracker cannot swallow the one issue that leaks a customer's data into a published artifact.

## Reproduction is not the triage axis

In both directions:

- **"It reproduces" is not "it is worth a number."** A manufactured backlog reproduces almost entirely — the reviewer measured the branch before filing. Reproduction confirms the mechanism and says nothing about the producer.
- **"I could not reproduce" is not a refutation.** A wrong path, a stale tree, or a premise you fed in yourself explains a failed reproduction at least as often as the issue being wrong. Locate the file, read the current ref, then judge.

## Spot-check every close with a silent failure mode

Before closing, ask: **if this verdict is wrong, does anyone find out?**

- Loud failure — a halt, a thrown error, a red run — is self-correcting. A wrong close costs one re-file.
- Silent failure — a green run over a corrupt deliverable, a masked leak, an artifact that diverges permanently and never self-corrects — is not. A wrong close there is shipped damage nobody attributes back to the tracker.

For every close in the second class, verify the mechanism yourself in current source instead of trusting the judgment that produced the verdict, whoever produced it. This includes your own.

## Every close gets a refutation, not a review

Ask a second opinion to **refute** the close. "Review my triage" produces agreement; agreement with a hostile prior is not corroboration.

Brief shape:

```
Issue #N. My verdict: close as <reason>.
My grounds: (1) function <F>, branch <B>; (2) no author outside <what I claim produces it>.
Your job is to refute, not to review. Return exactly one of:
  REFUTED  — name an author, the file they edit, and the ordinary input they write that
             reaches <F>'s <B>. No crafted strings: it must be something a person doing
             <the deliverable> would plausibly author.
  REFUTED-STALE — the body's premise is wrong but the defect is real. State the corrected
             mechanism from current source, citing file:line you opened.
  UPHELD   — state which of my two grounds you verified, and how.
Do not propose a remedy.
```

Forbidding the remedy is deliberate: a refuter that proposes a fix argues for the issue's importance instead of its reachability, which is the question.

## Fix the body before you act on it

A `premise-stale` correction lands before any disposition. An implementer reads the issue, not your triage — an issue scheduled with a false premise sends them the wrong way, and an issue closed on a premise nobody corrected gets refiled with the same error.

## Record what you did not do

The deliverable includes the parked set with an unpark condition per issue — a named consumer, never a date. A park with no condition is read as scheduled work by the next sweep, and refiled as new by the sweep after that.

Two more that cost nothing and are easy to skip:

- Never close A into B and B into A. Pick one survivor per group, explicitly.
- Never close an issue a shipped doc cites by number. The citation is live; closing orphans it. Park with a note. A *completed-fix marker* in shipped code — a comment explaining why a guard is shaped the way it is — is not that citation and does not block the close.

## Grep the citing files BEFORE the close, not after

A close has a blast radius in the repo: it silently falsifies every shipped sentence that still describes the issue as tracked, filed, or pending, and nothing in the tracker or the suite notices — a citation is prose, and the issue state lives on another host. Run this **before** closing, not as a follow-up:

```
grep -rnE "#<N>([^0-9]|$)" plugins/<plugin>/{skills,tests,commands,.claude-plugin}
```

Then classify each hit — the disposition differs by surface, and a uniform sweep over the results is wrong:

- **Live instruction prose** (`skills/**/references/*.md`, read and executed by a model) and **maintainer-facing comments** (test-suite prose that says where work is filed) → must state the decision. This is the class the "never close a cited issue" rule above protects.
- **Dated release copy** (`CHANGELOG.md` entries, the root `README.md`'s version-tagged notes) → leave it. Each was true when published; corrections go **forward**, never as a retroactive rewrite of a past entry — annotate in place only in the extreme case (one prior release carries a `> Superseded on the last sentence.` blockquote; that is the only precedent for touching one). A present-tense sweep over a changelog turns up many of these; rewriting them is history-editing, not a fix.

Measured 2026-08-18 (`enduser-handbook`): a triage closed nine issues, and four sentences in already-shipped files still described three of them as tracked, filed, or bounding (`#380` in the publish-target extension contract, `#341` twice in a test-suite file, `#577` in a library file) — the close comment on `#380` had even promised the pointer would be reworded in the next release, and that release shipped without it. Say which line you drew, in the release entry itself, or the next sweep re-files the historical hits as new work.

## Assert the survivors before you write

**Never close A into B when B is closing too.** The mutual case is easy to see. The transitive one is not, and it is what a multi-pass triage actually produces: a batch pass gives B `doc-is-the-fix` while a fold pass names B the survivor of A, and each pass is right about the question it was asked. Nothing in either pass looks at the other.

Resolve it mechanically, not by reading, and **before anything is written**: walk every survivor and assert none of them carries a closing disposition. A survivor that does means one of the two passes has to yield — and which one yields is a verdict, so re-derive it from source rather than deferring to whichever pass ran last. In the batch this rule came from, that re-derivation moved one issue to `real`, one to `close`, and left the third folded but with its remedy replaced.

Run the same assertion over the applied result too. A write-back is a program with its own defects, and a chain the audit never walks is indistinguishable from a chain that terminates.
