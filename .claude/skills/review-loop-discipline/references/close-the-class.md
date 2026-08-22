# Close the class

Use this when the same root cause appears at more than one site, layer, caller, format consumer, or verification rule.

## Start with the invariant

Describe the class as a property, not as the latest example. Then enumerate:

- every production site that can perform the operation;
- every layer from source to consumer that can drop, reinterpret, or bypass it;
- every producer and consumer of a changed shared value or format;
- every supported trust class that legitimately needs different behavior; and
- every lifecycle transition of a value used as an ambient proxy for a real event — a timestamp standing in for causal consumption, a flag standing in for delivery. Write, pre/post-write ordering, renewal, and failure paths are separate defect sites, not one shared assumption.

A reviewer normally cites one witness, not the population. Derive the population from the property that makes an item a member, not from the current helper, syntax, or convention; otherwise bypasses disappear from the census by construction. When delegating the enumeration to a reviewer, ask for the population directly — "is this the only chokepoint with this bug" — asking about the reported site alone gets the site confirmed, not the class found.

## Escalation ladder

1. **Fix the reported site.** Appropriate for a genuinely isolated defect.
2. **Fix the complete known set.** Appropriate only when the set is closed and independently enumerable.
3. **Share the implementation.** Reduces drift, but callers can still bypass a helper. A documented convention — a docstring telling callers what to check — is not this step: nothing forces a caller to read or obey it, so it drifts exactly like the bypass it was meant to prevent. Close the class with a function every chokepoint must call, not a paragraph they are supposed to remember.
4. **Make the alternative path unavailable.** Route the operation through one chokepoint, one table-driven loop, one authoritative materialized artifact, or a small allowlist of valid forms; then delete obsolete bypasses.

Move up only as recurrence justifies it. A structural rewrite is not automatically better than a few explicit sites. Compare added maintenance and test surface with the real consequence being removed.

## Structural patterns

- **Same-class findings across callers:** guard the operation at the narrowest common chokepoint, not only its current caller.
- **The outlier keeps escaping a table:** represent it as data in the same table instead of another special-case branch.
- **A checker reconstructs behavior already emitted elsewhere:** read the materialized output or authoritative state rather than recreating the decision from upstream proxies.
- **A matcher gains another example-shaped branch each round:** remove accidental anchors and express the actual relationship or property.
- **A denylist checker keeps gaining evasions:** require a bounded set of valid forms when the valid set is truly closed. If it is not closed, state the residual rather than pretending it is.
- **A custom mechanism is patched repeatedly:** check whether the platform already exposes the needed synchronization, atomicity, or validation primitive.
- **A widened gate is a half-fix if a sibling gate reads the same status literal:** grep every script that gates the same population before writing the plan. Two gates on one population move the wall to whichever is reached first; they do not remove it, and a gate that traverses a deliberately retained historical record can turn that record into a fresh way to abort a case it was never meant to block.

## Guards must justify their surface

A guard is structural only if every relevant path reaches it and the mechanism is no broader than the threat. Compare its maintenance cost and failure modes with the demonstrated product consequence. Prefer one hard-to-bypass chokepoint; do not turn a helper, text scan, or prose guarantee into a new loophole surface.

A guard's place matters as much as its coverage. If a case beneath it already refuses on its own, a test asserting only that the run refused cannot tell the two apart — but the guard's position now decides what the operator reads on the way out, and a message written for the coincidentally overlapping case can name the exact flag that causes the harm the guard exists to prevent. Put a guard above the case it is meant to own, not below one that happens to overlap it.

If policy permits a narrow set, encode the allowed space rather than growing a denylist. If no proportionate mechanism can express the rule, state the limitation or remove the feature instead of pretending the invariant is total. Before loosening or deleting verification, identify downstream consumers that relied on it and either re-establish the guarantee or name the new residual.

A pre-existing defect can be load-bearing by accident. A bug that misclassifies input can be the only thing currently blocking a destructive path, with no guard or test naming it as protection. When fixing such a defect, record what protection disappears with it — on the issue or in the fix's own description — or the next person removes the protection while believing they are only fixing a bug.

## Normative documents

A skill, runbook, workflow, or agent brief that tells a model or operator what to call is a production caller. At authoring time, verify named entrypoints, argument order, required context, and supported states against the real interface. Runtime tests cannot detect instructions that call correct code incorrectly or fail to mention the entrypoint at all.

When fixing a repeated prose class, keep one canonical rule and point other locations to it. Avoid present-tense censuses such as exact caller lists, suite totals, round counts, or line numbers; put volatile evidence in commits, tests, or incident notes instead.

## Completion check

The class is closed only when:

- the population and relevant layers were independently enumerated;
- the chosen structure covers that population or the residual is explicit;
- obsolete alternative paths are removed or intentionally justified; and
- the resulting mechanism is simpler or proportionate to the product risk.
