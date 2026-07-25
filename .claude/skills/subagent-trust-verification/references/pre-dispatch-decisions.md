# Resolve open decisions into a central doc before parallel dispatch

In a PARALLEL build, a brief item marked **OPEN / decide during implementation / lead decides / bring to review** is a **dispatch blocker, not a deferrable**. Parallelism turns one open question into N implementers each silently resolving it into divergent contract-visible behavior — and they cannot coordinate mid-build to converge.

This is distinct from the interface contract (data shapes, signatures, file ownership — see the main SKILL.md's Contract-first section). You can freeze every signature and data shape and still leave **behavioral policy** open: fail-closed-when-implied vs when-explicit? a `null` shape → on or off? which of two options for a two-option fix? does a fold reach a cross-module comparison? Each of those is a behavior an independent implementer will otherwise decide on its own, invisibly.

**A codex-clean plan is NOT the same as a dispatch-ready plan.** Verified 2026-07-18 (literary-translator appendix/matching two-lane train): the plan-review that session explicitly certified ~11 OPEN items "non-blocking for dispatch" — meaning non-blocking for CORRECTNESS. Codex reviews whether the plan is SOUND, not whether every decision is PINNED. Those are different gates, and the lead owns the second one.

**How to apply**, after the plan is codex-clean and BEFORE fan-out:

1. Grep every brief for `OPEN` / `lead decides` / `decide during impl` / `bring to review` / `undecided` / `TBD`.
2. Resolve EACH into a single `lead-decisions.md` all teammates reference. Take the conservative, minimal-blast-radius default on each; flag the consequential ones for user ratification (★) rather than silently committing the product to them.
3. Reference it from every dispatch prompt: "follow `lead-decisions.md`, do NOT re-decide."
4. Watch for a decision whose stated advantage is VOID given the rest of the changeset — re-price options against the WHOLE diff, not in isolation. (Verified same session: an alternative's headline "no render_version flip" benefit was void because another in-scope fix edited that hashed file anyway.)

**Why:** parallel implementers can't negotiate an unpinned decision mid-build; it becomes a silent fork that surfaces only at integration (or worse, in the shipped product) as two lanes that each did something locally reasonable but mutually inconsistent.
