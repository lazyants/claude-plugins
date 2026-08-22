# Close the class

Use this when the same root cause appears at more than one site, layer, caller, format consumer, or verification rule.

## Start with the invariant

Describe the class as a property, not as the latest example. Then enumerate:

- every production site that can perform the operation;
- every layer from source to consumer that can drop, reinterpret, or bypass it;
- every producer and consumer of a changed shared value or format; and
- every supported trust class that legitimately needs different behavior.

A reviewer normally cites one witness, not the population. Derive the population from the property that makes an item a member, not from the current helper, syntax, or convention; otherwise bypasses disappear from the census by construction.

## Escalation ladder

1. **Fix the reported site.** Appropriate for a genuinely isolated defect.
2. **Fix the complete known set.** Appropriate only when the set is closed and independently enumerable.
3. **Share the implementation.** Reduces drift, but callers can still bypass a helper.
4. **Make the alternative path unavailable.** Route the operation through one chokepoint, one table-driven loop, one authoritative materialized artifact, or a small allowlist of valid forms; then delete obsolete bypasses.

Move up only as recurrence justifies it. A structural rewrite is not automatically better than a few explicit sites. Compare added maintenance and test surface with the real consequence being removed.

## Structural patterns

- **Same-class findings across callers:** guard the operation at the narrowest common chokepoint, not only its current caller.
- **The outlier keeps escaping a table:** represent it as data in the same table instead of another special-case branch.
- **A checker reconstructs behavior already emitted elsewhere:** read the materialized output or authoritative state rather than recreating the decision from upstream proxies.
- **A matcher gains another example-shaped branch each round:** remove accidental anchors and express the actual relationship or property.
- **A denylist checker keeps gaining evasions:** require a bounded set of valid forms when the valid set is truly closed. If it is not closed, state the residual rather than pretending it is.
- **A custom mechanism is patched repeatedly:** check whether the platform already exposes the needed synchronization, atomicity, or validation primitive.

## Guards must justify their surface

A guard is structural only if every relevant path reaches it and the mechanism is no broader than the threat. Compare its maintenance cost and failure modes with the demonstrated product consequence. Prefer one hard-to-bypass chokepoint; do not turn a helper, text scan, or prose guarantee into a new loophole surface.

If policy permits a narrow set, encode the allowed space rather than growing a denylist. If no proportionate mechanism can express the rule, state the limitation or remove the feature instead of pretending the invariant is total. Before loosening or deleting verification, identify downstream consumers that relied on it and either re-establish the guarantee or name the new residual.

## Normative documents

A skill, runbook, workflow, or agent brief that tells a model or operator what to call is a production caller. At authoring time, verify named entrypoints, argument order, required context, and supported states against the real interface. Runtime tests cannot detect instructions that call correct code incorrectly or fail to mention the entrypoint at all.

When fixing a repeated prose class, keep one canonical rule and point other locations to it. Avoid present-tense censuses such as exact caller lists, suite totals, round counts, or line numbers; put volatile evidence in commits, tests, or incident notes instead.

## Completion check

The class is closed only when:

- the population and relevant layers were independently enumerated;
- the chosen structure covers that population or the residual is explicit;
- obsolete alternative paths are removed or intentionally justified; and
- the resulting mechanism is simpler or proportionate to the product risk.
