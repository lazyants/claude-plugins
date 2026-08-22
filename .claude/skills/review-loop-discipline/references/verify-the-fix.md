# Verify the fix

Verification must answer two different questions: was the requested edit applied, and did it remove the demonstrated consequence without creating a worse one? Passing the first does not imply the second.

## Minimum proof

For every admitted serious finding:

1. **Isolate the change.** Compare the post-fix tree with the same tree after reverting only the fix, or use an otherwise identical pre-fix snapshot. Two historical commits with unrelated differences do not attribute the result.
2. **Prove reachability.** Show the fixture or probe reaches the target decision and not an earlier guard, crash, alias, or different call ordinal. Prefer a trace, diagnostic, counter, or effect carrying identity from the real input.
3. **Show discrimination when constructible.** For a behavioral finding, the demonstrated case should fail before and pass after. An omission or prose defect may instead use direct artifact evidence, but still prove the claimed current state. An unchanged behavioral result is inconclusive.
4. **Cover the domain.** Test dominant real inputs, boundaries, legitimate variants, malformed or hostile variants inside the threat model, and the opposite over-correction direction—not only the ticket example.
5. **Compare failure modes.** Reject a fix that replaces a visible, recoverable failure with silent loss, false success, corruption, or a permanent wedge.
6. **Run the authoritative path.** Use the real implementation and entrypoint. A reimplementation of the expected behavior is not verification.

The same standard applies when refuting or downgrading a reviewer finding. Failure to reproduce proves only the topology that was actually built.

## When a fix adds a gate or validation layer

New machinery is new review surface.

- Exercise malformed configuration and the gate's own error path; it must fail clearly rather than crash or silently skip.
- Assert a plausible minimum population so a zero-match scan cannot pass.
- Mutate or remove the protected condition and confirm the intended test fails for the intended diagnostic.
- Re-audit existing negative tests: an upstream layer may now reject their fixtures before the downstream guard they were written to cover.
- Enumerate what the layer cannot express, such as cross-record identity, ordering, references, time, filesystem state, or user intent.
- For a new pipeline stage, exercise empty, fresh/missing, and rerun states, and join any closed registries or manifests that enumerate existing stages.
- Verify both acceptance and rejection. A restrictive fix can silently disable legitimate layouts that do not exist on the development machine.

A gate over prose can detect lexical drift, not semantic truth. Presence checks can pass on a negated instruction, and finite string pins can be contradicted by surrounding context. State that boundary instead of adding endless pins.

## Replacements and migrations

A rebuild or simpler mechanism inherits every behavior the old one supplied, including implicit behavior unrelated to the reported defect.

- Diff old and new outputs across the full relevant dataset and all required dimensions, not just the motivating cases. Verify every expected source partition is present before interpreting the comparison.
- For shared serialization, hashes, or schemas, enumerate every producer and consumer and migrate them together.
- A measured threshold and its enforcement gate must share the same predicate code, denominator rules, and boundary vectors.
- For collection changes, test what the structure discards: sets discard multiplicity, dictionaries may discard order, counts discard identity, and a new index may change fallback behavior.

## Plans, specifications, and routing prose

Fix the operative text, not its history.

- Replace the contradicted requirement in place. A correction added beside the old instruction leaves two valid-looking requirements.
- Search the claim across the whole artifact using its distinctive identifiers; a finding normally cites only one restatement.
- After deleting a feature or requirement, classify every remaining mention as current rule, rationale, or unmistakable history.
- Keep the active artifact current-state only. Review chronology and superseded alternatives belong in the session, commit, or a separate audit record.
- When a reference gains a new case, update the router in the same change so the case can be selected. When shortening a router, preserve the selection concepts rather than merely moving words into a body that loads only after selection.
- Verify replacement wording positively; absence of the old phrase does not prove the new statement is accurate.

## Common false confidence

- **Reviewer-authored remedy:** authority does not exempt the remedy from review. Attack the chosen data structure and assumptions independently.
- **Green fixture:** assert the fixture exhibits the abnormal state before asserting behavior about it.
- **Coarse failure class:** assert the diagnostic and subject, not merely `ok: false`; an earlier guard or crash can produce the same class.
- **Call/existence proxy:** assert content and provenance that only the real operation could produce.
- **Coverage by reasoning:** a rule counts as covered only when a discriminating negative control was executed.

## Completion check

Do not say `fixed`, `ready`, or `refuted` until the evidence names:

- the isolated delta;
- the real path reached;
- the pre-fix and post-fix discriminating result;
- domain and over-correction coverage;
- regression checks across replaced behavior;
- the authoritative environment used; and
- any residual risk or verification boundary.
