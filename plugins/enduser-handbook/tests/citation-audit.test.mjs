// Citation-direction lint tests (#258). Runs under Node's built-in runner:
// `node --test citation-audit.test.mjs` (explicit path — `node --test <dir>` gives a
// misleading MODULE_NOT_FOUND).
// The shell suite (reference-assets.test.sh) auto-discovers every tests/*.test.mjs and runs it here,
// gated on `command -v node`.
//
// Coverage mirrors the plan's PR3 Verification list:
//   - non-vacuity guard (a nonzero, exact citation total; a zero-returning scanner fails loudly)
//   - the mechanically-enforced UNRESOLVED allowlist, keyed by true per-occurrence identity
//     {file, offset, quotedText, direction} — asserted EXACTLY (no superset = a new unresolved
//     citation fails; no subset = a stale entry that became resolvable fails; direction in the key =
//     a direction flip on an unresolved citation fails)
//   - uniqueness guard: no citation resolves ambiguously to 2+ same-title headings
//   - direction assertion: every resolved citation's stated above/below matches its heading's real
//     position (this is the red-before-green gate for the two live obsidian-vault.md bugs)
//   - synthetic fixtures: a wrapped citation, a fenced-code decoy (must be excluded), a no-verb
//     citation (round-3 scope broadening), a same-line/same-title/opposite-direction pair (proves the
//     offset key distinguishes two occurrences a line-only key would collapse), and an ambiguous
//     duplicate-heading fixture.
//
// The pinned numbers below are content-fragile BY DESIGN. A failure here means a citation was added,
// removed, moved, or flipped in the reference docs and must be re-reviewed — which is exactly the
// drift this lint exists to catch. Re-derive them against source (do not trust any stale number):
//   node -e "import('./citation-audit-lib.mjs').then(m => { const r = m.auditCorpus();
//     console.log(r.length, r.filter(x=>x.status==='unresolved').length); })"

import test from 'node:test';
import assert from 'node:assert/strict';

import { auditCorpus, auditText, extractCitations } from './citation-audit-lib.mjs';

// Total citation occurrences across references/**/*.md + SKILL.md (resolved + unresolved). Re-derived
// against source 2026-07-27 after the writer-prose truth pass removed one redundant cross-reference
// and the present-halt recovery correction shifted the touched adapters' offsets; pinned to the
// fresh measurement, never a number quoted in a plan. The retired occurrence was a RESOLVED one:
// obsidian-vault.md's "Nested-list automation limits" (below), which pointed at a heading it really
// did resolve to. That is why the unresolved allowlist below is unchanged at 42 entries while the
// total drops by one: a near-miss would have
// had to leave this list too. Read the two numbers together whenever this pin moves; a total that
// moves alone is the only shape consistent with a resolved citation being the one that went.
const EXPECTED_TOTAL_CITATIONS = 94;

// Every citation whose quoted text does NOT resolve to exactly one heading title in its own file — an
// over-match, a near-miss (e.g. "INDEX wiring" vs the full parenthetical heading), or a title that
// simply is not a heading ("Coordinate systems"), plus the one heading whose literal double-quotes
// (`What "Obsidian vault" implies`) cannot be cited inside a "…"-delimited citation. Keyed by the
// absolute character offset of the occurrence, so two same-title citations (even on one line, even
// with opposite directions) never collapse into one entry.
// Re-pinned for 1.11.0 (#329/#330): the doc edits shifted every offset at or after the first insertion
// point in each touched file, and added two new near-miss citations — both reviewed as legitimate,
// same pattern as the pre-existing entries for the same headings: obsidian-vault.md's new
// "Nested-list automation limits" prose cites "INDEX wiring" (above), and static-md.md's equivalent
// prose cites "Grouped index wiring" (above). In both the cited heading really does sit above the
// citation point.
// Fix round 2026-07-27: the short operator-facing recovery-class explanation added under
// "Nested-list automation limits" in both adapters moved each later offset by 325 characters.
// Composition was proved unchanged before editing any offset: all 94
// {file, quotedText, direction} records retained SHA-256
// f12d567b99e00b295e4b2642b78e6c59bd1bd02c0d363a86483a49e184aed6c4, and the 42 unresolved
// records retained SHA-256 d04f0bd8fe01b058f4d3896881e7cb27c8b565f0711c85421c64d3f205fe6fd0.
// Identified by file and section deliberately, NOT by offset. An earlier revision of this comment
// named offsets 35057 and 27814; the very next commit re-derived the table below and left the prose
// behind, so this file — whose whole job is pinning re-derived measurements — carried two numbers
// that matched no citation in the corpus. Nothing went red, because only the table is asserted and
// the prose is the unasserted half. A file/section reference cannot go stale that way.
// Round-4 doc scoping added three entries. TWO are the long-standing near-miss shape already
// represented above (a heading cited by a shortened title whose real heading carries a
// parenthetical). The THIRD is a DIFFERENT shape and is called out so this list is not read as
// homogeneous: static-md.md cites "Grouped entry, line present, `indexForm: 'headings'`", which is
// a BULLET label, not a heading — no heading of that name exists anywhere. The reference points at
// something real and is more precise for a reader than citing its enclosing heading would be; it is
// unresolved only because this lint models headings and nothing else. Recorded rather than reworded
// so the limitation sits with the lint, where it belongs, instead of bending the prose to it.
//
// 1.11.0 review added six more entries across rounds 9-13, and this comment went stale behind them
// once already before being caught — the same drift it records above, one layer up. So it no longer
// enumerates entries at all: the table below is the enumeration, and a count in prose can only
// disagree with it. Two SHAPES are new and are worth naming, because neither is the
// heading-with-a-parenthetical case above:
//   * a citation to the opening words of a PROSE PARAGRAPH rather than to any label
//     (obsidian-vault.md's "Path mode scans") — weaker than the bullet-label shape, and the only
//     reason it is tolerable is that the paragraph it names is stable and directly above;
//   * a citation to a BOLDED SENTENCE used as a paragraph lead-in (static-md.md's "The headings
//     branch is unchanged by this PR and already completes silently"), which reads as a heading to
//     a human and as nothing at all to this lint.
// Every entry's DIRECTION was verified by hand when added, because an unresolved citation is also a
// direction-unchecked one: the lint cannot compare against a heading it never found.
const EXPECTED_UNRESOLVED = [
  { file: 'references/diataxis.md', offset: 2877, quotedText: 'When this is the right shape', direction: 'below' },
  { file: 'references/profile-validation.md', offset: 11273, quotedText: '`inline` stays minimal', direction: 'below' },
  { file: 'references/profile-validation.md', offset: 14551, quotedText: 'Cross-line structural validation', direction: 'above' },
  { file: 'references/publish-targets/obsidian-vault.md', offset: 1544, quotedText: 'Coordinate systems', direction: 'below' },
  { file: 'references/publish-targets/obsidian-vault.md', offset: 3290, quotedText: 'Coordinate systems', direction: 'below' },
  { file: 'references/publish-targets/obsidian-vault.md', offset: 5281, quotedText: 'INDEX wiring', direction: 'below' },
  { file: 'references/publish-targets/obsidian-vault.md', offset: 6333, quotedText: 'What \'Obsidian vault\' implies', direction: 'above' },
  { file: 'references/publish-targets/obsidian-vault.md', offset: 6700, quotedText: 'INDEX wiring', direction: 'below' },
  { file: 'references/publish-targets/obsidian-vault.md', offset: 9709, quotedText: 'Chapter structure', direction: 'below' },
  { file: 'references/publish-targets/obsidian-vault.md', offset: 27629, quotedText: 'Non-headings index, no existing line', direction: 'below' },
  { file: 'references/publish-targets/obsidian-vault.md', offset: 28320, quotedText: 'Path mode scans', direction: 'above' },
  { file: 'references/publish-targets/obsidian-vault.md', offset: 29208, quotedText: 'Non-headings index', direction: 'below' },
  { file: 'references/publish-targets/obsidian-vault.md', offset: 38116, quotedText: 'The placement check is retained unchanged (D-8)', direction: 'above' },
  { file: 'references/publish-targets/obsidian-vault.md', offset: 45114, quotedText: 'Measured, across every placement', direction: 'below' },
  { file: 'references/publish-targets/obsidian-vault.md', offset: 48360, quotedText: 'Non-headings index, no existing line', direction: 'above' },
  { file: 'references/publish-targets/obsidian-vault.md', offset: 56684, quotedText: 'Container resolution', direction: 'above' },
  { file: 'references/publish-targets/obsidian-vault.md', offset: 60377, quotedText: 'INDEX wiring', direction: 'above' },
  { file: 'references/publish-targets/obsidian-vault.md', offset: 60617, quotedText: 'Non-headings index, no existing line', direction: 'above' },
  { file: 'references/publish-targets/obsidian-vault.md', offset: 60767, quotedText: 'Measured, across every placement', direction: 'above' },
  { file: 'references/publish-targets/obsidian-vault.md', offset: 62685, quotedText: 'Non-headings index, no existing line', direction: 'above' },
  { file: 'references/publish-targets/obsidian-vault.md', offset: 68401, quotedText: 'INDEX wiring', direction: 'above' },
  { file: 'references/publish-targets/obsidian-vault.md', offset: 70191, quotedText: 'INDEX wiring', direction: 'above' },
  { file: 'references/publish-targets/obsidian-vault.md', offset: 74450, quotedText: 'INDEX wiring', direction: 'above' },
  { file: 'references/publish-targets/obsidian-vault.md', offset: 79677, quotedText: 'INDEX wiring', direction: 'above' },
  { file: 'references/publish-targets/static-md.md', offset: 12961, quotedText: 'Chapter path', direction: 'above' },
  { file: 'references/publish-targets/static-md.md', offset: 15014, quotedText: 'Grouped index wiring', direction: 'below' },
  { file: 'references/publish-targets/static-md.md', offset: 15381, quotedText: 'Chapter → index', direction: 'above' },
  { file: 'references/publish-targets/static-md.md', offset: 18937, quotedText: 'Grouped index wiring', direction: 'below' },
  { file: 'references/publish-targets/static-md.md', offset: 19082, quotedText: 'Grouped index wiring', direction: 'below' },
  { file: 'references/publish-targets/static-md.md', offset: 26203, quotedText: 'After either halt', direction: 'below' },
  { file: 'references/publish-targets/static-md.md', offset: 31839, quotedText: 'Grouped index wiring', direction: 'above' },
  { file: 'references/publish-targets/static-md.md', offset: 34897, quotedText: 'The plain-label predicate, named exactly', direction: 'below' },
  { file: 'references/publish-targets/static-md.md', offset: 39662, quotedText: 'Grouped entry, line present, `indexForm: \'non-heading\'`', direction: 'above' },
  { file: 'references/publish-targets/static-md.md', offset: 41320, quotedText: 'Grouped entry, line present, `indexForm: \'headings\'`', direction: 'above' },
  { file: 'references/publish-targets/static-md.md', offset: 44765, quotedText: 'The plain-label predicate, named exactly', direction: 'below' },
  { file: 'references/publish-targets/static-md.md', offset: 45823, quotedText: 'The plain-label predicate, named exactly', direction: 'below' },
  { file: 'references/publish-targets/static-md.md', offset: 47455, quotedText: 'Grouped index wiring', direction: 'above' },
  { file: 'references/publish-targets/static-md.md', offset: 49060, quotedText: 'After either halt', direction: 'above' },
  { file: 'references/publish-targets/static-md.md', offset: 51372, quotedText: 'Grouped index wiring', direction: 'above' },
  { file: 'references/publish-targets/static-md.md', offset: 52289, quotedText: 'Grouped index wiring', direction: 'above' },
  { file: 'references/publish-targets/static-md.md', offset: 54052, quotedText: 'The headings branch is unchanged by this PR and already completes silently', direction: 'above' },
  { file: 'references/publish-targets/static-md.md', offset: 62663, quotedText: 'Grouped index wiring', direction: 'above' },
];

// Per-occurrence key. offset alone is already unique; file/quotedText/direction are folded in so a
// drift shows a human-readable diff and a direction flip on an unresolved citation also fails.
function occKey(r) {
  return `${r.file}\0${r.offset}\0${r.quotedText}\0${r.direction}`;
}

const CORPUS = auditCorpus();

test('non-vacuity: the scanner finds a nonzero, exact citation total (#258)', () => {
  assert.ok(CORPUS.length > 0, 'scanner found ZERO citations — a vacuous "all directions correct" pass');
  assert.equal(
    CORPUS.length,
    EXPECTED_TOTAL_CITATIONS,
    `citation total drifted from ${EXPECTED_TOTAL_CITATIONS} (found ${CORPUS.length}) — a citation was added/removed/moved; re-review and re-pin`,
  );
});

test('non-vacuity guard fails loudly when a (broken) scanner returns zero (#258)', () => {
  // The guard the real test above relies on. A scanner that silently matches nothing must FAIL the
  // suite, not vacuously pass "every citation is correctly directed". This proves the > 0 assertion
  // actually throws on zero rather than being a no-op.
  const brokenScannerCount = 0;
  assert.throws(
    () => assert.ok(brokenScannerCount > 0, 'vacuous'),
    'a zero citation count must throw, not pass',
  );
});

test('unresolved allowlist matches EXACTLY — no superset, no subset, direction-keyed (#258)', () => {
  const actual = CORPUS.filter((r) => r.status === 'unresolved');
  const actualKeys = actual.map(occKey).sort();
  const expectedKeys = EXPECTED_UNRESOLVED.map(occKey).sort();
  // Exact set equality in both directions: a NEW unresolved citation (superset) or a stale entry that
  // became resolvable (subset) both fail here, as does a direction flip on an already-unresolved one.
  assert.deepEqual(
    actualKeys,
    expectedKeys,
    'unresolved citation set drifted from the pinned allowlist — inspect the diff; a new entry needs review, a vanished one needs cleanup',
  );
});

test('uniqueness guard: no citation resolves ambiguously to 2+ same-title headings (#258)', () => {
  const ambiguous = CORPUS.filter((r) => r.status === 'ambiguous').map(
    (r) => `${r.file}:${r.line} "${r.quotedText}" matches headings @${r.matchLines.join(',')}`,
  );
  assert.deepEqual(ambiguous, [], 'a citation resolved to multiple same-title headings — must not silently pick one');
});

test('direction assertion: every resolved citation states the correct above/below (#258)', () => {
  const wrong = CORPUS.filter((r) => r.status === 'resolved' && !r.directionOk).map(
    (r) => `${r.file}:${r.line} "${r.quotedText}" says ${r.direction} but heading @${r.heading.line} is ${r.expectedDirection}`,
  );
  assert.deepEqual(wrong, [], `wrong-direction citation(s):\n${wrong.join('\n')}`);
});

// ---------------------------------------------------------------------------------------------
// Synthetic fixtures — small hand-built inputs that lock specific behaviors independent of the corpus.
// ---------------------------------------------------------------------------------------------

test('synthetic: a citation wrapped across a source line break resolves to its single-line heading', () => {
  const text = [
    '## Relative links',
    '',
    'intro prose',
    '',
    'The rule (see "Relative',
    'links" above) still applies.',
  ].join('\n');
  const recs = auditText(text);
  assert.equal(recs.length, 1, 'exactly one citation');
  assert.equal(recs[0].quotedText, 'Relative links', 'wrapped quote is whitespace-collapsed before matching');
  assert.equal(recs[0].status, 'resolved');
  assert.equal(recs[0].directionOk, true, 'heading is above the citation and the citation says above');
});

test('synthetic: a citation-shaped string inside a fenced code block is excluded by the mask', () => {
  const fenced = ['## Foo', '', '```', '"Foo" below', '```', ''].join('\n');
  assert.equal(extractCitations(fenced).length, 0, 'a fenced citation-shaped string must NOT be matched');
  // Control: the identical string OUTSIDE a fence IS matched, proving the fence mask (not a broken
  // regex) is what excluded it.
  const unfenced = ['## Foo', '', '"Foo" below', ''].join('\n');
  assert.equal(extractCitations(unfenced).length, 1, 'the same string outside a fence is matched');
});

test('synthetic: a quoted title with NO introducing verb is still matched (round-3 scope broadening)', () => {
  const text = ['## Layout', '', 'the "Layout" below is what you get'].join('\n');
  const recs = auditText(text);
  assert.equal(recs.length, 1, 'no-verb citation is extracted');
  assert.equal(recs[0].quotedText, 'Layout');
  assert.equal(recs[0].status, 'resolved', 'and resolves to the heading — the matcher anchors on proximity, not a verb');
});

test('synthetic: same-line, same-title, opposite-direction pair keeps distinct offsets (allowlist key)', () => {
  // Mirrors codex round-3: a {file,line,quotedText,direction} key cannot tell these two apart on a
  // swap; the absolute offset can. "Missing" is not a heading, so both are unresolved.
  const text = 'For A, see "Missing" above; for B, see "Missing" below.';
  const recs = auditText(text);
  assert.equal(recs.length, 2, 'two citations on one line');
  assert.deepEqual(recs.map((r) => r.quotedText), ['Missing', 'Missing'], 'same quoted title');
  assert.deepEqual(recs.map((r) => r.direction).sort(), ['above', 'below'], 'opposite directions');
  assert.deepEqual(recs.map((r) => r.status), ['unresolved', 'unresolved']);
  assert.equal(new Set(recs.map((r) => r.line)).size, 1, 'both on the SAME line — a line-only key would collapse them');
  assert.equal(new Set(recs.map((r) => r.offset)).size, 2, 'but DISTINCT offsets — the offset key distinguishes them');
});

test('synthetic: two headings sharing a title make a citation AMBIGUOUS, not silently resolved', () => {
  const text = ['## Dup', 'a', '## Dup', 'b', 'see "Dup" above'].join('\n');
  const recs = auditText(text);
  assert.equal(recs.length, 1);
  assert.equal(recs[0].status, 'ambiguous', 'a title matched by 2+ headings must be flagged, never picked');
  assert.deepEqual(recs[0].matchLines, [1, 3]);
});

// Security review (2026-07-24): a long run of quoted decoys with NO trailing direction word is a
// doomed match. Two shapes were found and fixed here, both against exactly this kind of input:
//   1. The original span regex's separator, `\s*(?:[,;:]|and\b)?\s*` (two adjacent `\s*`s sandwiching
//      an optional middle group), hit EXPONENTIAL backtracking (~26 repeats already took 8+ seconds).
//      Fixed by collapsing it into one quantified alternation, `(?:[\s,;:]|\band\b)*`.
//   2. That fix alone still left the outer matching SHAPE — one monolithic regex retried at every
//      quote-start position via `matchAll` — QUADRATIC on this input (review-bot finding: 29ms at
//      2,000 titles growing to 1.64s at 16,000). Fixed by replacing the whole approach with the
//      single-forward-pass chain-growing algorithm in extractCitations (see its doc comment).
// This test pins the absence of a false match AND — since an absolute bound at one N cannot tell
// linear from quadratic from exponential apart — the actual SCALING behavior across two sizes, so a
// future change that reintroduces either superlinear shape fails loudly instead of silently
// reintroducing a hang.
test('extractCitations does not catastrophically backtrack on a long undirected quoted-title run (ReDoS regression)', () => {
  // #343: this asserts a RATIO of two wall-clock durations, and BOTH ends were unsound. Measured on
  // this branch (804 tests) against the 616-test pre-release baseline, so the trigger is suite LOAD,
  // not the regex — which is untouched across this release.
  //   1. DENOMINATOR, resolution. `Date.now()` gave the 5,000-title run 0-1ms, and the old
  //      `Math.max(small, 1)` floor then pinned it at 1, degenerating the "ratio" into the large
  //      run's absolute duration. Fixed by `process.hrtime.bigint()`, a nanosecond monotonic clock.
  //   2. NUMERATOR, scheduling. That alone still failed 1 run in 4. The reason is NOT resolution:
  //      measured on the failures, `small` was a healthy 0.76-1.43ms while `large` inflated to
  //      57-94ms against a ~16ms baseline — the big run was being descheduled mid-measurement, a
  //      real 4-6x, so the ratio cleared 40 with nothing wrong.
  // A single timing sample cannot distinguish "slow because quadratic" from "slow because preempted".
  // Repeating and taking the MINIMUM can: preemption and GC only ever ADD time, so the minimum of k
  // samples converges on the uncontended cost from above, while genuine quadratic scaling is present
  // in every sample and survives the min untouched.
  // The two assertions below then read DIFFERENT statistics, on purpose, because they ask different
  // questions. The SCALING ratio wants the uncontended cost, so it uses the minimum. The ABSOLUTE
  // blow-up bound wants the worst thing that actually happened, so it uses the maximum. An earlier
  // revision of this comment said the absolute bound stayed single-sample while `timeFor` returned
  // only the minimum — that was simply false, and it meant four 2.5-second runs plus one fast one
  // would sail through a guard whose entire job is noticing that the run took seconds.
  const TIMING_SAMPLES = 5;
  const timeFor = (n) => {
    const decoyRun = '"a" '.repeat(n) + 'end.';
    let best = Infinity;
    let worst = 0;
    for (let i = 0; i < TIMING_SAMPLES; i += 1) {
      const start = process.hrtime.bigint();
      const recs = extractCitations(decoyRun);
      const elapsedMs = Number(process.hrtime.bigint() - start) / 1e6;
      assert.deepEqual(recs, [], `n=${n}: no trailing above/below means no citation span should match at all`);
      if (elapsedMs < best) best = elapsedMs;
      if (elapsedMs > worst) worst = elapsedMs;
    }
    return { best, worst };
  };
  // The STATISTIC was the problem, not the input size and not the ceiling. Taking the minimum of
  // each side SEPARATELY does nothing when one side is inflated across all its samples, and the
  // noise runs both ways — measured in-suite, single paired ratios at these sizes ranged 3.2x to
  // 108.7x, the low end meaning the SMALL run was the one that got hit.
  // Two things were tried and measured before this one, and both are recorded so they are not
  // retried: enlarging both inputs to 20k/320k made the in-suite median WORSE (56.7x vs 15.8x,
  // both pairs measured in the same runs) because at 320,000 titles the working set leaves cache
  // and per-title cost rises; and raising the ceiling to 120x still failed 1 run in 20.
  // What works is a ratio that is itself robust: take PAIRS paired (small, large) measurements and
  // use the MEDIAN of the ratios, so one contaminated pair cannot move the verdict in either
  // direction. Measured in-suite over 16 full-suite runs, the median-of-five statistic ranged
  // 18.7x-45.1x where the single-sample one reached 108.7x. The 100x ceiling sits 2.2x above the
  // worst healthy sample seen and 2.6x below the ~256x a quadratic regression produces at 16x
  // input. Re-measure that distribution before changing the number; do not tighten it toward 16x
  // "because linear should be 16x" — measured, it is not.
  const PAIRS = 5;
  const ratios = [];
  let worstLarge = 0;
  for (let i = 0; i < PAIRS; i += 1) {
    const s = timeFor(5_000);
    const l = timeFor(80_000); // 16x the input
    ratios.push(l.best / s.best);
    if (l.worst > worstLarge) worstLarge = l.worst;
  }
  ratios.sort((a, b) => a - b);
  const ratio = ratios[Math.floor(ratios.length / 2)];
  // Linear ⇒ ~16x in theory and ~21x measured; quadratic ⇒ ~256x; exponential ⇒ unmeasurably larger.
  assert.ok(
    worstLarge < 2000,
    `expected EVERY 80,000-title run well under 2s, slowest took ${worstLarge}ms — possible ReDoS regression`,
  );
  assert.ok(
    ratio < 100,
    `16x input took ${ratio.toFixed(1)}x longer (median of ${PAIRS} paired samples) — ` +
      'that is quadratic-or-worse scaling, not linear; possible ReDoS regression',
  );
});
