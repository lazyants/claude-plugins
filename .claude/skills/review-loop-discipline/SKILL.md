---
name: review-loop-discipline
description: Techniques for working the fix → simplify → codex → security-review loop well — knowing when a loop is healthy vs a rabbit hole and when to exit a verifier/gate loop, closing a bug CLASS instead of patching instances, handling the new review surface a fix's own layer/gate/guarantee spawns, catching a net-negative fix that trades a visible failure for a silent one, verifying a fix covers its whole DOMAIN (not just the ticket's example) and that a rebuild didn't regress a dimension the original got right, reproducing a CI/gate failure in a clean env, and scope-gating a heavy optional component out of a bundled fix.
---

# Review-loop discipline

Procedural know-how for the iterative fix → code-simplifier → codex → security-review loop: how to shape a fix so the next round doesn't re-find it, how to tell a converging loop from a thrashing one, how to prove the fix is actually right, and how to keep scope from ballooning. These are deliberate techniques for during the loop — the always-on reflexes (red-before-green, run all three reviewers) live elsewhere.

Two through-lines run under everything below. **Close the CLASS, not the instance:** when a reviewer keeps returning new cases with the same root cause, stop patching locations, enumerate the whole set, and state the invariant so the reviewer verifies the class is closed. **Know when to stop:** a loop that keeps DELETING complexity and shifting findings from architectural to wording is converging; one adding normalization layers to chase an ambiguous or impossible property is not — exit deliberately, don't wait for the reviewer to say CLEAN forever.

Read the reference for the situation you're in:

- **references/converge-and-exit.md** — running the loop itself: healthy-vs-rabbit-hole signals, why finding-COUNT isn't the health metric, the deletion pivot, A/B finding classification, fencing ratified decisions, the stop criterion for a verifier/gate loop, and the loop's exit condition. Read when deciding whether to keep looping or how to end one.
- **references/close-the-class.md** — same-class whack-a-mole → enumerate + state the invariant; serialization/format migrations across every consumer; the completeness-GREP gate (and how to verify the gate itself isn't a no-op); algorithm-internal dedup and core-data-structure swaps; symbolic vs line-number refs. Read when the reviewer keeps finding new instances of one root cause.
- **references/fix-adds-review-surface.md** — a fix that ADDS a validation layer / gate / guarantee spawns its own review surface; new pipeline scripts must satisfy the pipeline's other invariants; guarantee-words and architecturally-impossible guarantees. Read when your fix introduces new machinery rather than changing a line.
- **references/net-negative-fixes.md** — comparing the fix's own worst failure to the bug's, erring toward the visible/recoverable direction, escalating a fundamentally-ambiguous tradeoff instead of whack-a-mole. Read when about to fix a reviewer's edge-case flag with a new gate/heuristic on an unreliable signal.
- **references/verify-the-fix.md** — verifying the fix covers the DOMAIN not just the ticket example, that a rebuild didn't regress or prove the wrong axis, source-completeness before trusting a rebuild, cross-checking your own artifacts, and reproducing the gate's clean env. Read before saying "done" or "ready".
- **references/scope-gating.md** — gating a heavy optional component out FIRST, the mid-loop descope tell, the attestation-downgrade valve, and escalating an emergent scope balloon by tiers. Read when a plan/fix bundles a discretionary component or its scope grows past the issue.
