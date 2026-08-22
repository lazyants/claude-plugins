# Converge and exit

The governing review policy and cap live in `~/.claude/CLAUDE.md`. This reference explains how to use that bound. It must not create a second reviewer policy.

## Freeze before judging convergence

A verdict is useful only for the snapshot it inspected. For any round intended to clear the work:

- identify the exact file, diff, commit, or tree;
- stop concurrent edits until the verdict returns;
- include the requested outcome, non-goals, trust boundary, and accepted risks;
- state which findings from prior rounds are ratified decisions rather than open questions;
- name the weakest suspected seam as a neutral question with a real `no` answer; and
- separate open-ended discovery from verification of one admitted fix.

If the artifact changes after the verdict, follow the governing procedure for full-tree versus fix-range review. This reference cannot widen or narrow that requirement.

## Read the shape of the tail

Finding count alone is not convergence. Use the location and cause of findings.

### Healthy

- serious findings concern the original artifact and become narrower;
- accepted fixes remove special cases or reduce code and prose;
- the requested scope and threat model remain stable; and
- verification addresses a known fix rather than opening new territory.

### Self-feeding

- findings increasingly target guards, tests, or justification added by earlier rounds;
- each fix adds another normalization, state, exemption, or veto;
- serious findings plateau in one mechanism for two rounds; or
- the artifact grows while the protected product path is already sound.

Respond by asking for the structural cause, then simplify, relocate, or delete the review-added mechanism. A guard whose own defect and maintenance surface exceed the drift it catches is net negative.

### Non-convergent

The loop is non-convergent when valid asks are mutually exclusive, the required capability does not exist at the chosen layer, or each choice merely swaps one bounded failure for another. Do not run another discovery round to re-observe the fork.

1. State the competing consequences and whether one option dominates.
2. Prefer fail-loud, recoverable, and bounded behavior over silent loss, false success, or a permanent wedge.
3. Recommend the smallest safe outcome. Name the narrowest true blocker and check what existing machinery can deliver before rejecting a partial improvement solely because it cannot close the whole class.
4. Ask the user only when the choice changes an explicit requirement, scope, or accepted material risk.
5. Record the residual and fence it from re-litigation in later prompts.

A reviewer request to eliminate an accepted residual does not justify new machinery by itself.

## Scope control

### Optional components

When a plan includes a heavy discretionary component, decide inclusion before discussing its internals. Offer omission or deferral as the first real alternative. If repeated serious findings concentrate on that optional component while the rest converges, split it into a follow-up with the findings preserved as input.

### Adjacent findings

A real adjacent defect is not automatically part of the current change. File or record it separately unless it makes the requested outcome impossible or unsafe. Do not silently turn a narrow fix into a repository-wide hardening program.

### Impossible guarantees

A mechanism cannot defend a property against an actor with the same authority over the data and enforcement surface. When that is the threat model, the answer is a different capability boundary—sandboxing, privilege separation, or confinement—not another same-layer check. Otherwise narrow the guarantee to the supported trust model and state the residual.

## Stop conditions

Stop discovery at the predeclared cap even when the last round was hot. A quiet round is not proof of completeness; a hot round is not permission to extend the budget.

The work is ready only when:

- every required review lane has completed against the relevant frozen tree;
- no admitted `fix-now` finding stands;
- accepted fixes were verified under the governing procedure;
- required tests and runtime checks are green; and
- residuals, deferrals, and scope decisions are explicit.

If the cap fires with an admitted blocker or major still standing, report `bounded review completed; not ready` and redesign, cut scope, or escalate the real decision. Never convert cap exhaustion into reviewer approval.

## Report

Report the bound, dispositions with evidence, verification performed, and residual risk. Do not narrate round history or claim `clean` merely because the loop stopped.
