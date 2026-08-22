# Capture-guard facts: detection scope, and drift in its copyable fixtures

Distinct from §3's capture-**safety** PII surface-audit (`surface-audit.playwright.ts`,
`control-inventory.mjs`) — this file covers the capture-**guard** network-route filter
(`assets/lib/capture-guard-policy.mjs`, `decideRoute`, `references/capture-spec-helpers.md`) and its
copyable example fixtures (`assets/capture.example.spec.ts`, `assets/reaudit.example.spec.ts`).

## #471: the redirect-hop audit channel is DETECTION, not prevention

`context.route` never sees a redirect hop — verified against real chromium on playwright-core 1.61.1
AND 1.62.1, not read off the source. The #471 fix (`46c95cb`'s predecessor, shipped in 1.13.0) adds a
**second, audit-only** channel on `context.on('request')` that re-classifies each hop by its own
method/URL/body through the same `decideRoute` (`assets/lib/capture-guard-policy.mjs:243`). A
dangerous hop lands in the ledger and fails the run — **but it has already fired.** Real prevention
would need `route.fetch()` with manual redirect following; that is explicitly out of scope for the
shipped fix. Read the index's "redirect-hop audit channel" phrasing literally: it is an audit, not a
block.

Regression coverage is `tests/capture-guard-redirect-wiring.test.mjs` +
`tests/capture-guard-redirect-wiring.fixture.mjs` (a TypeScript driver run under
`--experimental-strip-types`), added specifically because codex found the listener→ledger seam had no
executable test.

**Open question, not a gap:** Dedicated-Worker hop coverage is unestablished from static reading alone
(~80% confidence there is no actual gap) — it needs a runtime fixture, not another read of the source.

**The design error behind this surface, filed as #557:** the "what gets admitted" narrative is
re-derived from scratch in several independent prose paragraphs in `capture-spec-helpers.md`
(`tests/reference-assets.test.sh:1497` names it), none tied to `decideRoute`'s actual branch order.
Three consecutive review rounds each found a DIFFERENT paragraph making a claim the guard does not
back, and round 3's defect was introduced by round 2's own rewrite — the grep pins only pin sentences
already known wrong, so they can't catch the next paraphrase. The structural fix (not done, filed as
follow-up): state the branch chain once, canonically, pinned to the `// [guard:*]` sentinels the suite
already asserts on real code, and let prose cover consequences only.

## Duplicate copyable skeleton drift — retire the second copy, don't mirror the correction

A measured enumeration (which requests the removed `denyPatterns` guarantee actually covered) was
written into **two** copyable skeletons an adopter clones: `capture.example.spec.ts` and
`reaudit.example.spec.ts`. A later review round corrected one and left the other false — two copies of
a measured list drift by construction, and the second copy is exactly where nobody looks next.

**When a review round corrects a measured claim in one of these skeletons, the fix is to retire the
second copy and point it at the first, not to mirror the correction into it.** This is stronger than
§6's "enumerate all write sites and fix them" for this specific surface class (copyable fixtures meant
to diverge in every dimension except a shared measured claim): mirroring keeps two texts that must stay
byte-identical forever, which is the same failure mode one edit away; retiring removes the second
site entirely.

## `object[data]` does NOT miss a script-assigned `data`

A PR body (#566) shipped a false mechanism claim: that an attribute-qualified `object[data]` selector
would miss a `data` attribute assigned by a script after page load. It is untrue — CSS attribute
selectors are evaluated against the **live DOM at query time**, so a script-time assignment is visible
to `object[data]` exactly like a parse-time one. The claim stood in three places at once: the PR body,
the copyable asset (`assets/capture-helpers.playwright.ts` — the unqualified-selector comment near
line 1022), and the suite's own comments (`tests/reference-assets.test.sh:1433` and `:1450`, which
narrate the old wrong justification before the corrected one). Fixing it required enumerating the whole
plugin for the same two claims, not just the one site the round started at — the same fix-one-site-of-N
defect the fixing branch was itself trying to close.

## Related

- SKILL.md §3 (capture-safety PII surface-audit) — a different capture mechanism in this plugin;
  don't conflate `control-inventory.mjs`'s over-capture problem with `capture-guard-policy.mjs`'s
  detection-only redirect audit.
- SKILL.md §6 (multi-write-site trap for a link-emission canon change) — the general shape the
  duplicate-skeleton drift above is a stronger instance of.
