# Scope-gating during the loop

Clarifying questions are a scarce, interrupting resource, and a fix's scope can grow past its issue during review. Manage both with explicit scope levers.

## Gate a heavy optional component FIRST

When a plan bundles a large, discretionary, process-shaped component (a second-project pilot, a migration, a status-promotion pass, a "nice to have" verification run) alongside concrete code fixes, the FIRST scope question about that component must offer **"exclude it / not this round / defer"** as an explicit option — not only variants of *how* to do it. Spending clarifying questions on the internals of a component whose very inclusion is optional wastes a round and forces the user to invent the "exclude" answer via "Other". Only after inclusion is confirmed do you ask about its internals. This is especially true for anything heavy, external-resource-dependent (needs a book/dataset/credential the user must supply), or process- rather than code-shaped.

## The mid-loop descope tell

When CONSECUTIVE adversarial-review rounds keep concentrating their HIGH findings on the SAME optional sub-feature while the rest of the plan converges, that concentration IS the escalation trigger — stop patching and offer descope via AskUserQuestion with an HONEST cost comparison: e.g. "descope kills findings F1–F4 outright, ~1–2 rounds to clean" vs "harden: new durable state + fs-transaction machinery, 3+ rounds." A sub-feature whose every fix demands NEW contract surface (provenance records, rollback state machines, schema fields) is the shape to watch. When descoped, the FOLLOW-UP issue carries the hardened design + all reviewer findings as input, so the work isn't lost — it's staged for its own ratification, and the descoped plan is a much tighter PR.

## The second valve: attestation downgrade

When the findings re-concentrate on a completion-VERIFICATION mechanism rather than a feature, the resolution is NOT another descope: downgrade the unverifiable checks to red-flag + explicit USER confirmation. Boundary: that downgrade is a refinement WITHIN an already-ratified descope, so decide it DIRECTLY and flag it for veto in the status message — an AskUserQuestion there would burn a scarce interrupt on a non-scope call. Pre-announce the valve to the user BEFORE the next verdict arrives ("if round N concentrates again, I'll offer X") — pre-announcing makes acting on it friction-free.

## Emergent scope-balloon → escalate by tiers

A fix's scope can grow PAST its issue's stated framing during the review loop — the classic case is a docs-honesty issue, where a doc lie is rarely confined to the one file the issue names and the reviewer surfaces the same fiction across many files round after round. Do NOT silently do the full sweep (over-engineering the user may steer against) NOR silently under-scope (leaves the reviewer-flagged incoherence) — escalate with AskUserQuestion offering the TIERS: user-facing-only + track-the-rest / full-sweep-now / literal-issue-only.

The issue's framing is not ground truth for scope — it can OVERSTATE a premise or UNDER-scope a systemic problem. When it under-scopes, anchor the fix to the filed issue's exact claim and FILE the adjacent defects the reviewer surfaces as NEW issues, stating the explicit SCOPE BOUNDARY in the plan so the reviewer evaluates the principle and returns clean rather than expanding a "small" PR without bound.
