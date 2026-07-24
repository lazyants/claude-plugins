// Citation-direction lint tests (#258). Runs under Node's built-in runner: `node --test
// citation-audit.test.mjs` (explicit path — `node --test <dir>` gives a misleading MODULE_NOT_FOUND).
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
// against source 2026-07-24; the count grew every round of plan review, so it is pinned to the fresh
// measurement, never a number quoted in the plan.
const EXPECTED_TOTAL_CITATIONS = 47;

// Every citation whose quoted text does NOT resolve to exactly one heading title in its own file — an
// over-match, a near-miss (e.g. "INDEX wiring" vs the full parenthetical heading), or a title that
// simply is not a heading ("Coordinate systems"), plus the one heading whose literal double-quotes
// (`What "Obsidian vault" implies`) cannot be cited inside a "…"-delimited citation. Keyed by the
// absolute character offset of the occurrence, so two same-title citations (even on one line, even
// with opposite directions) never collapse into one entry.
const EXPECTED_UNRESOLVED = [
  { file: 'references/diataxis.md', offset: 2877, quotedText: 'When this is the right shape', direction: 'below' },
  { file: 'references/profile-validation.md', offset: 11273, quotedText: '`inline` stays minimal', direction: 'below' },
  { file: 'references/profile-validation.md', offset: 14551, quotedText: 'Cross-line structural validation', direction: 'above' },
  { file: 'references/publish-targets/obsidian-vault.md', offset: 1544, quotedText: 'Coordinate systems', direction: 'below' },
  { file: 'references/publish-targets/obsidian-vault.md', offset: 3290, quotedText: 'Coordinate systems', direction: 'below' },
  { file: 'references/publish-targets/obsidian-vault.md', offset: 5281, quotedText: 'INDEX wiring', direction: 'below' },
  { file: 'references/publish-targets/obsidian-vault.md', offset: 6333, quotedText: "What 'Obsidian vault' implies", direction: 'above' },
  { file: 'references/publish-targets/obsidian-vault.md', offset: 6700, quotedText: 'INDEX wiring', direction: 'below' },
  { file: 'references/publish-targets/obsidian-vault.md', offset: 9709, quotedText: 'Chapter structure', direction: 'below' },
  { file: 'references/publish-targets/obsidian-vault.md', offset: 32939, quotedText: 'INDEX wiring', direction: 'above' },
  { file: 'references/publish-targets/obsidian-vault.md', offset: 38166, quotedText: 'INDEX wiring', direction: 'above' },
  { file: 'references/publish-targets/static-md.md', offset: 12759, quotedText: 'Chapter path', direction: 'above' },
  { file: 'references/publish-targets/static-md.md', offset: 14812, quotedText: 'Grouped index wiring', direction: 'below' },
  { file: 'references/publish-targets/static-md.md', offset: 15179, quotedText: 'Chapter → index', direction: 'above' },
  { file: 'references/publish-targets/static-md.md', offset: 18756, quotedText: 'Grouped index wiring', direction: 'below' },
  { file: 'references/publish-targets/static-md.md', offset: 30162, quotedText: 'Grouped index wiring', direction: 'above' },
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

// Security review (2026-07-24): a long comma-joined run of quoted decoys with NO trailing direction
// word is a doomed match that must backtrack all the way through CITATION_SPAN_RE's outer `+` — the
// original `\s*(?:[,;:]|and\b)?\s*` separator (two adjacent `\s*`s sandwiching an optional middle
// group) hit catastrophic backtracking here (verified: ~26 repeats already took 8+ seconds, growing
// exponentially). The fix collapsed the separator into one quantified alternation,
// `(?:[\s,;:]|\band\b)*` — this test pins BOTH the absence of a false match AND a tight time bound,
// so a future "simplification" that reintroduces the adjacent-optional shape fails loudly instead of
// silently reintroducing the hang.
test('extractCitations does not catastrophically backtrack on a long undirected quoted-title run (ReDoS regression)', () => {
  const decoyRun = '"a" '.repeat(2000) + 'end.';
  const start = Date.now();
  const recs = extractCitations(decoyRun);
  const elapsed = Date.now() - start;
  assert.deepEqual(recs, [], 'no trailing above/below means no citation span should match at all');
  assert.ok(elapsed < 500, `expected well under 500ms, took ${elapsed}ms — possible ReDoS regression`);
});
