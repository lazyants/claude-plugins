# Capturing `reference-assets.test.sh` output — the FAIL/TOTAL stream split

`plugins/enduser-handbook/tests/reference-assets.test.sh` splits its output across two streams:

- `bad()` (near the top, with the other assertion helpers) — `printf "  FAIL  %s\n" "$1" >&2` — every
  failing check's NAME goes to **stderr**.
- The summary on the last line — `echo "TOTAL: $PASS/$TOTAL passed, $FAIL failed"` — goes to **stdout**.

(Grep for those two strings rather than trusting a line number: this file grows by a dozen lines every
release, and the summary's number moved twice on the day this was written. Both strings re-verified
verbatim against the live suite on 2026-08-02.)

So a run captured without stderr, or piped through `tail -N`, reports `TOTAL: 712/713 passed, 1 failed`
and shows **no indication of which check failed**. The ~700 passing `ok` lines push any FAIL line far
outside a tail window even when stderr IS merged, because the FAIL is emitted at the point of failure,
not collected at the end.

Verified 2026-07-30 (enduser-handbook 1.12.0, round-6 declaration sweep): a single 712/713 run was
followed by 13 consecutive 713/713 runs, and the failing check's identity was unrecoverable — the
`2>&1 | tail -15` that produced the count had discarded the name. The team-lead independently hit the
same wall on the same suite and only got the name by reading stderr explicitly. The lost name is what
forced the cause to be inferred from mtimes instead of read off the output, and the inference landed on
the wrong actor — see (→skill:subagent-trust-verification) for that incident and why a transient
failure in this repo is usually someone's live mutation rather than a flake.

**How to apply:** never capture this suite with a bare `tail`. Grep both streams for the failure marker
and the summary together, so a failing run names itself on the spot (run from the plugin root,
`plugins/enduser-handbook/`):

```bash
bash tests/reference-assets.test.sh 2>&1 | grep -E '^  FAIL |^TOTAL:'
```

Re-running until it passes and reporting the green is the failure mode this guards against: the count
alone cannot distinguish a real regression from a transient one, and the name is gone by then.

Related: the absolute PASS/FAIL total is environment-dependent (the `esbuild`-gated check adds 0 or 1
assertion), and a plugin-subtree `git archive` test bed separately carries 17 permanent repo-root-reaching
failures — see the "Review discipline" section of SKILL.md before quoting a total in release copy or a
mutation-review report.

## Reconcile a suite-count delta by DIFFING the two check-name sets, never by arithmetic

`#420`/`#339` (PR #575) shipped a PR body that carried a false measured claim for several review
rounds — "origin/main also reads 958/958, delta zero" — because nothing re-measured it once the delta
*sounded* reconciled. Both numbers in that claim were wrong; the actual baseline was 955/955 and the
branch measured 959/959.

Arithmetic on two totals cannot tell you whether a delta is real or is two unrelated changes that
happen to cancel. **Diff the two runs' check-NAME sets instead** (`bash reference-assets.test.sh` names
each check as it runs) — that is what actually resolved this delta: −6 retired needles (the old
VALUE-side per-name pins), +3 restored needles (the type-only pins under new names — see
`references/declaration-parity-facts.md`), +5 census pins, +2 `node --test` entries, net +4 on a base
of 955 landing at 959. A bare "959 vs 955, delta 4, looks right" check would have passed even if the
composition were completely different from the actual −6/+3/+5/+2 breakdown — the set diff is the only
way to confirm the delta is made of the SPECIFIC changes the PR claims, not just the right count.
