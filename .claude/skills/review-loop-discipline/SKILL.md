---
name: review-loop-discipline
description: Use inside an existing plan or code review loop when a defect class recurs, a fix adds guard or gate machinery, scope expands, or convergence fails — and before any finding is filed as a tracker issue. Adjudicate the tail, close defect classes, verify fixes, gate what earns an issue number, and stop honestly. For the verdict on an issue that is already open, use skill:tracker-triage.
---

# Review-loop discipline

Use adversarial review to reduce release risk, not to generate unlimited scope or defensive machinery.

`~/.claude/CLAUDE.md` owns the mandatory reviewer lanes, severity admission rule, and round cap. Follow it when anything here differs. This skill adds the operating method; it does not add reviewers or extend the cap.

## Core loop

1. **Freeze the contract.** Record the exact artifact or tree, requested outcome, acceptance criteria, non-goals, stakes, trust boundary, and accepted risks. A deciding review must inspect an unchanged snapshot.
2. **Adjudicate before editing.** A reviewer label is a claim. For each serious finding, verify the mechanism, reachability, consequence, causation, and scope. The proposed remedy is a second untrusted claim; reconcile it against constraints from every review lane, because reviewer agreement may share one blind spot.
3. **Choose the smallest sufficient response.** Use one disposition: `fix-now`, `refuted`, `downgraded-nonblocking`, `defer-follow-up`, `accepted-tradeoff`, or `scope-cut`. Only `fix-now` changes implementation automatically. A scope cut or material risk choice goes to the user.
4. **Fix the class when it recurs.** Two findings with the same root cause trigger a class-wide search. If the class returns again, stop patching sites: simplify, centralize the invariant, use a platform primitive, move the decision to the layer with the required capability, or remove reviewer-invented machinery.
5. **Verify the actual consequence.** Prove the accepted fix reaches the real path, distinguishes pre-fix from post-fix behavior, covers the relevant domain, and has not replaced a visible failure with a silent or destructive one.
6. **Stop by policy, not reviewer mood.** Discovery and fix verification are different rounds. Do not extend discovery because the last round was hot, or claim completeness because it was quiet. At the cap, report bounded review honestly. A standing `fix-now` means the artifact is not ready; deferred or accepted residuals must be named.

## From finding to tracker issue

A `defer-follow-up` is not yet an issue. Before opening a number, name both:

- the shipped **function** whose branch is wrong — one you have opened, not one you remember; and
- a **person who is not a reviewer** who reaches that branch doing their own work.

Missing either name, open nothing. Then take the first case that matches:

- **An open issue already names that function.** Add a measured row to it. One wrong branch is one issue however many inputs reach it; input spellings are rows, never numbers.
- **"Which function" answers with a doc sentence.** The fix *is* that sentence. Edit it at the next touch of the file and file nothing.
- **The case for it is symmetry with a neighbouring branch.** Symmetry is a code shape, not a producer. Name who emits the input, or drop the finding.
- **The proposed remedy is a module, transaction, or extra return value.** Size the remedy for the real consumer; state what one accurate sentence of prose would do instead, and who would read it.

A finding raised in a closing round, or against a file outside the frozen artifact, meets the same two names — a late or out-of-scope finding gets a higher bar, not a lower one.

## Reference routing

- Read [converge-and-exit.md](references/converge-and-exit.md) when deciding whether to continue, descope, accept a residual, or terminate a non-convergent loop.
- Read [close-the-class.md](references/close-the-class.md) when the same root cause returns, a guard keeps growing, or a check reconstructs behavior available from a more authoritative source.
- Read [verify-the-fix.md](references/verify-the-fix.md) before declaring a finding fixed or refuted, and whenever a fix adds a gate, replaces a mechanism, or edits a plan/specification.
- Read [finding-to-issue.md](references/finding-to-issue.md) before filing findings as new tracker issues, or when a round is producing more issues than fixes. Defending a verdict on a number that is already open is `skill:tracker-triage`'s job, not this one's.
