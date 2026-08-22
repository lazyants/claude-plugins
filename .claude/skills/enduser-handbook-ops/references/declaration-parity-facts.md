# Declaration/runtime parity gate (#420 + #339) — settled facts, do not re-litigate

`tests/declaration-parity.test.mjs` + `tests/export-parity-lib.mjs` gate that every `.d.mts` export
matches its runtime counterpart (names both directions, declared arity as a RANGE against
`Function.prototype.length`, orphan modules both ways including a declaration-only zombie).
`tests/reference-assets.test.sh` carries a related but SEPARATE census of three fixed-string,
type-only needles.

## SETTLED: the three type-only pins stay retired-then-restored — this has been re-proposed twice

`#420`/`#339` retired the three VALUE-side per-name needles of #330 (old lines 3191-3208), then
**retired and RESTORED** the three type-only ones after two independent reviewers pushed to drop them
for good. **Do not re-propose dropping them a third time without reading this first.**

- **What the three pins actually guarantee, measured:** deletion of the pinned type goes red; a
  **suffix rename does NOT**, because each needle is a prefix of its own renamed form. That is the
  full and correct scope of the guarantee — it is not a general type-existence census.
- **Why "drop them, the census covers it" is wrong:** the argument for dropping conflated the
  fixed-string pins with a DIFFERENT thing — a general type-existence census, which does need a
  TS-lib allowlist (#339's own 31-false-red design problem). The three pins are narrower and don't
  need that allowlist; the census's cost is not a reason to drop the pins.
- **What DID retire and should stay retired:** the per-release RECOUNT rule (re-deriving the total
  every release) — that is genuinely subsumed by the census block. Only the recount rule, not the
  three pins themselves.

If a future reviewer proposes dropping the three type-only pins again, point at this file rather than
re-deriving the argument — it has already been made and rejected twice on the same reasoning.

## Related

- `references/reference-assets-suite-output.md` — reconciling this suite's PASS/TOTAL delta by
  diffing check-name sets, the technique that resolved this same PR's own false measured claim.
- SKILL.md §14 — `tests/declaration-parity.test.mjs` and `tests/export-parity-lib.mjs` are among the
  files that joined the chapter-paths hub component for backlog-partitioning purposes via
  `tests/skill-call-signatures.test.mjs`; that is a file-ownership fact, unrelated to the pins above.
