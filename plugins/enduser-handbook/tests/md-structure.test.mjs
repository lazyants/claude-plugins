// Unit tests for the Markdown heading-tree parser / fence-masking primitive (#303). Zero deps
// beyond node:fs/node:path/node:url for reading the real static-md.md adapter doc — runs under
// Node's built-in test runner: `node --test md-structure.test.mjs` (explicit path — `node --test
// <dir>` gives a misleading MODULE_NOT_FOUND).
//
// Coverage mirrors the plan's PR2 Verification list: every ported fence rule 1:1 against the awk
// `_section_contains` engine's intent -> exact-heading-match/first-binding -> the three
// sectionStatus states (incl. heading-absent as its own bucket, empty-needle) -> the corrected
// half-open-interval boundary (last-line-before-boundary, true EOF) -> findOwner nesting ->
// prototype-chain headings -> a static-md-shaped integration fixture -> the 6 real branch-ownership
// pins (uniqueness guards + the decoy/moved-occurrence mutant fixture, mirroring the
// chapter-paths.test.mjs:~1958-1977 exactly-one-occurrence precedent).

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  maskFencedRegions,
  parseHeadings,
  findOwner,
  sectionStatus,
} from '../skills/enduser-handbook/assets/lib/md-structure.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const STATIC_MD_PATH = join(
  HERE,
  '../skills/enduser-handbook/references/publish-targets/static-md.md',
);

// ---------------------------------------------------------------------------------------------
// Local grep-equivalent helpers — line-based, matching the shell suite's count_fixed/line_of so a
// unit-side uniqueness guard measures the SAME thing the bash `assert_line_before` witnesses do.
// ---------------------------------------------------------------------------------------------

// grep -cF: number of LINES containing the needle (never total occurrences).
function lineCountContaining(text, needle) {
  return text.split('\n').filter((line) => line.includes(needle)).length;
}

// grep -nF | head -n1 | cut -d: -f1: the 1-based line number of the needle's first occurrence.
function firstLineOf(text, needle) {
  const idx = text.indexOf(needle);
  return idx === -1 ? -1 : text.slice(0, idx).split('\n').length;
}

// The heading-titles a parse yields, in document order — the coarse "did this `#` line count as a
// heading" observable most fence-rule tests key on.
function titles(text) {
  return parseHeadings(text).map((h) => h.title);
}

// ---------------------------------------------------------------------------------------------
// Ported fence rules — 1:1 with the awk `_section_contains` engine. Each rule is proven in BOTH
// directions (fence opens/doesn't; closes/doesn't) via whether a `## Ghost` heading sitting inside
// the candidate fence is masked (absent from parseHeadings) or survives (present).
// ---------------------------------------------------------------------------------------------

test('fence rule: opener indent gate — <=3 spaces opens (Ghost masked), >=4 spaces does NOT (treated as live text, Ghost survives)', () => {
  const opens = '## Real\n   ```\n## Ghost\n   ```\n## After\n'; // 3-space indent
  assert.deepEqual(titles(opens), ['Real', 'After']);
  const stays = '## Real\n    ```\n## Ghost\n    ```\n## After\n'; // 4-space indent
  assert.deepEqual(titles(stays), ['Real', 'Ghost', 'After']);
});

test('fence rule: closer indent gate — a >=4-space closer does NOT close (fence stays open), a <=3-space closer does', () => {
  const text = '```\n## Ghost1\n    ```\n## Ghost2\n```\n## After\n';
  // opener at indent 0; the 4-space ``` at line 3 cannot close, so the fence swallows Ghost1 AND
  // Ghost2, until the indent-0 ``` at line 5 finally closes it.
  assert.deepEqual(titles(text), ['After']);
});

test('fence rule: minimum run length 3 — `` (2) is not a fence (Ghost survives), ``` (3) is (Ghost masked)', () => {
  const twoTicks = '## Real\n``\n## Ghost\n``\n## After\n';
  assert.deepEqual(titles(twoTicks), ['Real', 'Ghost', 'After']);
  const threeTicks = '## Real\n```\n## Ghost\n```\n## After\n';
  assert.deepEqual(titles(threeTicks), ['Real', 'After']);
});

test('fence rule: closer must match the opener CHAR — ~~~ never closes a ``` fence (both stay masked until a real ``` closer)', () => {
  const text = '```\n## Ghost\n~~~\n## Ghost2\n```\n## After\n';
  assert.deepEqual(titles(text), ['After']);
});

test('fence rule: closer run length — a shorter run does NOT close a longer opener; a run >= the opener length does', () => {
  // 4-backtick opener: a 3-backtick line cannot close it, a 4-backtick line does.
  const longOpener = '````\n## Ghost\n```\n## Ghost2\n````\n## After\n';
  assert.deepEqual(titles(longOpener), ['After']);
  // 3-backtick opener: a 4-backtick line DOES close it (run >= openLen).
  const longerCloser = '```\n## Ghost\n````\n## After\n';
  assert.deepEqual(titles(longerCloser), ['After']);
});

test('fence rule: backtick opener info string may not contain a backtick (not an opener); tildes are exempt', () => {
  // ```foo`bar — the info string carries a backtick, so this is ordinary text, not a fence opener.
  const backtick = '## Real\n```foo`bar\n## Ghost\n## After\n';
  assert.deepEqual(titles(backtick), ['Real', 'Ghost', 'After']);
  // ~~~foo`bar — tildes have no info-string backtick rule, so this DOES open a fence.
  const tilde = '## Real\n~~~foo`bar\n## Ghost\n~~~\n## After\n';
  assert.deepEqual(titles(tilde), ['Real', 'After']);
});

test('fence rule: a tab-led line never fences (#257) — neither opens nor closes', () => {
  // A tab before ``` reports 0 leading SPACES and leaves the tab as the first char, which is never
  // a fence marker: no fence opens, so ## Ghost survives.
  const tabOpener = '## Real\n\t```\n## Ghost\n\t```\n## After\n';
  assert.deepEqual(titles(tabOpener), ['Real', 'Ghost', 'After']);
  // A tab-led ``` cannot CLOSE an open fence either — the real (indent-0) ``` at the end does.
  const tabCloser = '```\n## Ghost\n\t```\n## Ghost2\n```\n## After\n';
  assert.deepEqual(titles(tabCloser), ['After']);
});

test('fence rule: CRLF normalized — a \\r\\n-delimited fence masks correctly and heading.raw is CR-stripped', () => {
  const text = '## Real\r\n```\r\n## Ghost\r\n```\r\n## After\r\n';
  const heads = parseHeadings(text);
  assert.deepEqual(heads.map((h) => h.title), ['Real', 'After']);
  assert.deepEqual(heads.map((h) => h.raw), ['## Real', '## After']); // no trailing \r leaks into raw
});

test('fence rule: consecutive-fence close-state reset — content between two fences (## Mid) is live, both fence bodies masked', () => {
  const text = '```\nA\n```\n## Mid\n```\nB\n```\n## After\n';
  assert.deepEqual(titles(text), ['Mid', 'After']);
  const masked = maskFencedRegions(text);
  assert.ok(!masked.includes('A') || masked.split('\n')[1].trim() === ''); // fence-1 body blanked
  assert.ok(masked.includes('## Mid')); // the live heading between the two fences survives masking
  assert.ok(!masked.split('\n')[5].includes('B')); // fence-2 body blanked
});

test('fence rule: opener info string is opaque — everything after the run on the opener line is masked with the fence', () => {
  const text = '```markdown ## not-a-heading\n## Ghost\n```\n## After\n';
  assert.deepEqual(titles(text), ['After']); // neither the info string nor ## Ghost is parsed
  const openerLine = maskFencedRegions(text).split('\n')[0];
  assert.equal(openerLine.trim(), ''); // the whole opener line, info string included, is blanked
});

test('maskFencedRegions is character-offset- and line-position-preserving', () => {
  const text = '## Keep\nvisible\n```\nhidden ## nope\n```\n## Keep2\n';
  const masked = maskFencedRegions(text);
  assert.equal(masked.length, text.length); // total length preserved
  assert.equal(masked.split('\n').length, text.split('\n').length); // newline count/position preserved
  assert.ok(masked.includes('## Keep\nvisible\n')); // non-fenced content byte-identical
  assert.ok(masked.includes('## Keep2')); // heading after the fence untouched
  assert.ok(!masked.includes('hidden')); // fenced content blanked
  assert.ok(!masked.includes('nope'));
});

// ---------------------------------------------------------------------------------------------
// Exact-heading binding, first-occurrence, prefix-decoy rejection — mirrors the awk's
// `line == heading && found_heading == 0`.
// ---------------------------------------------------------------------------------------------

test('sectionStatus / findOwner bind by EXACT heading line — a prefix (## Target extra) never binds ## Target', () => {
  const text = '## Target extra\ndecoy-body\n## Target\nreal-body\n## Next\n';
  // sectionStatus must find real-body under the real ## Target, and NOT decoy-body (which lives
  // under the prefix-only ## Target extra).
  assert.equal(sectionStatus(text, '## Target', 'real-body'), 'found');
  assert.equal(sectionStatus(text, '## Target', 'decoy-body'), 'needle-absent');
  // findOwner of the exact heading's own body line resolves to the exact node, not the prefix one.
  const heads = parseHeadings(text);
  const exact = heads.find((h) => h.raw === '## Target');
  assert.equal(findOwner(heads, firstLineOf(text, 'real-body')), exact);
});

test('binding is first-occurrence only on a repeated heading title', () => {
  const text = '## Dup\nfirst-body\n## Dup\nsecond-body\n## End\n';
  // The engine binds the FIRST ## Dup; second-body lives under the second ## Dup, out of the
  // first's section -> needle-absent.
  assert.equal(sectionStatus(text, '## Dup', 'first-body'), 'found');
  assert.equal(sectionStatus(text, '## Dup', 'second-body'), 'needle-absent');
});

// ---------------------------------------------------------------------------------------------
// The three sectionStatus states — the JS-side mirror of the bash 3-way engine (#302).
// ---------------------------------------------------------------------------------------------

test('sectionStatus: heading-absent is its OWN state, never folded into needle-absent (#302 mirror)', () => {
  const text = '## Real\nbody\n';
  assert.equal(sectionStatus(text, '## Ghost Heading', 'body'), 'heading-absent');
  assert.equal(sectionStatus(text, '## Real', 'body'), 'found');
  assert.equal(sectionStatus(text, '## Real', 'no-such-needle'), 'needle-absent');
});

test('sectionStatus: an empty needle buckets as needle-absent (matches the awk needle != "" guard)', () => {
  const text = '## Real\nbody\n';
  assert.equal(sectionStatus(text, '## Real', ''), 'needle-absent');
  // heading-absent still wins over the empty-needle rule when the heading itself is missing.
  assert.equal(sectionStatus(text, '## Ghost', ''), 'heading-absent');
});

test('sectionStatus: a needle only inside a fenced block is NOT found (fence mask), a live one is', () => {
  const text = '## S\nreal-needle\n```\nfenced-needle\n```\n';
  assert.equal(sectionStatus(text, '## S', 'real-needle'), 'found');
  assert.equal(sectionStatus(text, '## S', 'fenced-needle'), 'needle-absent');
});

test("sectionStatus: a section spans its deeper nested headings (awk in_section semantics), and closes at the next same-or-shallower heading", () => {
  const nested = '## H2\n### H3\ndeep-needle\n';
  assert.equal(sectionStatus(nested, '## H2', 'deep-needle'), 'found'); // deeper content is in-section
  const closes = '## A\na-needle\n## B\nb-needle\n';
  assert.equal(sectionStatus(closes, '## A', 'a-needle'), 'found');
  assert.equal(sectionStatus(closes, '## A', 'b-needle'), 'needle-absent'); // ## B closed ## A's section
});

// ---------------------------------------------------------------------------------------------
// The corrected half-open interval [bodyStart, bodyEndExclusive) — the off-by-one an inclusive
// bodyEnd would have introduced at every boundary and at EOF.
// ---------------------------------------------------------------------------------------------

test('half-open interval: the last line before a boundary heading belongs to the section BEFORE it, never leaks into the next', () => {
  const text = '## A\nlast-A-line\n## B\nb-body\n';
  const heads = parseHeadings(text);
  const a = heads.find((h) => h.raw === '## A');
  const b = heads.find((h) => h.raw === '## B');
  assert.equal(findOwner(heads, firstLineOf(text, 'last-A-line')), a);
  assert.notEqual(findOwner(heads, firstLineOf(text, 'last-A-line')), b);
  assert.equal(sectionStatus(text, '## A', 'last-A-line'), 'found');
  assert.equal(sectionStatus(text, '## B', 'last-A-line'), 'needle-absent');
});

test('half-open interval: the true EOF line belongs to the last open heading — no off-by-one overflow (with and without a trailing newline)', () => {
  const noNl = '## A\nfirst\neof-line';
  const heads1 = parseHeadings(noNl);
  const a1 = heads1.find((h) => h.raw === '## A');
  assert.equal(findOwner(heads1, firstLineOf(noNl, 'eof-line')), a1);
  assert.equal(sectionStatus(noNl, '## A', 'eof-line'), 'found');
  // bodyEndExclusive is lineCount + 1, so the last real line is strictly inside the interval.
  assert.equal(a1.bodyEndExclusive, noNl.split('\n').length + 1);

  const withNl = '## A\nfirst\neof-line\n';
  const heads2 = parseHeadings(withNl);
  const a2 = heads2.find((h) => h.raw === '## A');
  assert.equal(findOwner(heads2, firstLineOf(withNl, 'eof-line')), a2);
  assert.equal(sectionStatus(withNl, '## A', 'eof-line'), 'found');
});

// ---------------------------------------------------------------------------------------------
// findOwner nesting.
// ---------------------------------------------------------------------------------------------

test('findOwner: a line in an H2 own body before its first child H3 resolves to the H2', () => {
  const text = '## H2\nh2-own-body\n### H3\nh3-body\n';
  const heads = parseHeadings(text);
  const h2 = heads.find((h) => h.raw === '## H2');
  assert.equal(findOwner(heads, firstLineOf(text, 'h2-own-body')), h2);
});

test('findOwner: a line under a second sibling H3 is not misattributed to the first', () => {
  const text = '## H2\n### H3a\na-body\n### H3b\nb-body\n';
  const heads = parseHeadings(text);
  const h3a = heads.find((h) => h.raw === '### H3a');
  const h3b = heads.find((h) => h.raw === '### H3b');
  assert.equal(findOwner(heads, firstLineOf(text, 'b-body')), h3b);
  assert.notEqual(findOwner(heads, firstLineOf(text, 'b-body')), h3a);
});

test('findOwner: three-deep H2>H3>H4 — a line inside the H4 resolves to the H4, not an ancestor', () => {
  const text = '## H2\n### H3\n#### H4\nh4-body\n';
  const heads = parseHeadings(text);
  const h2 = heads.find((h) => h.raw === '## H2');
  const h4 = heads.find((h) => h.raw === '#### H4');
  assert.equal(findOwner(heads, firstLineOf(text, 'h4-body')), h4);
  assert.notEqual(findOwner(heads, firstLineOf(text, 'h4-body')), h2);
});

test('findOwner: a line before the first heading has no owner (null)', () => {
  const text = 'preamble\n## A\nbody\n';
  const heads = parseHeadings(text);
  assert.equal(findOwner(heads, firstLineOf(text, 'preamble')), null);
});

// ---------------------------------------------------------------------------------------------
// Prototype-chain fixtures — a heading literally titled `## constructor` / `## toString` must not
// corrupt any lookup. This module uses only linear Array.find over raw text (no title-keyed object
// index), so these resolve like any other heading; the test locks that in against a future
// regression to `obj[title]`-style access (the class tests/profile-schema-evaluator.test.mjs hit).
// ---------------------------------------------------------------------------------------------

test('prototype-chain headings: ## constructor / ## toString bind and resolve like any other heading', () => {
  const text = '## constructor\nctor-body\n## toString\ntostr-body\n## hasOwnProperty\nhop-body\n';
  assert.equal(sectionStatus(text, '## constructor', 'ctor-body'), 'found');
  assert.equal(sectionStatus(text, '## toString', 'tostr-body'), 'found');
  assert.equal(sectionStatus(text, '## constructor', 'tostr-body'), 'needle-absent');
  const heads = parseHeadings(text);
  const ctor = heads.find((h) => h.raw === '## constructor');
  assert.equal(findOwner(heads, firstLineOf(text, 'ctor-body')), ctor);
  assert.deepEqual(heads.map((h) => h.title), ['constructor', 'toString', 'hasOwnProperty']);
});

test('prototype-chain headings: a prototype-method-named heading that is ABSENT returns heading-absent, never a phantom hit', () => {
  const text = '## Real\nbody\n'; // no ## toString anywhere
  assert.equal(sectionStatus(text, '## toString', 'body'), 'heading-absent');
  assert.equal(sectionStatus(text, '## constructor', 'body'), 'heading-absent');
});

// ---------------------------------------------------------------------------------------------
// Integration fixture modeled on static-md.md's real shape: an H2 (with content in its own body)
// followed by a nested child H3 plus a sibling H3.
// ---------------------------------------------------------------------------------------------

test('integration fixture (static-md-shaped): H2-own-body content, a nested H3, and a sibling H3 each resolve to the right node', () => {
  const doc = [
    '# Publish target',
    '',
    '## Index wiring',
    'flat-outcome sentinel',
    'locateChapterLine call sentinel',
    '',
    '### Grouped index wiring',
    'grouped-only content',
    '',
    '### Manual group migration',
    'migration content',
    '',
    '## Glossary',
    'glossary content',
    '',
  ].join('\n');
  const heads = parseHeadings(doc);
  const h2 = heads.find((h) => h.raw === '## Index wiring');
  const grouped = heads.find((h) => h.raw === '### Grouped index wiring');
  const migration = heads.find((h) => h.raw === '### Manual group migration');
  const glossary = heads.find((h) => h.raw === '## Glossary');
  // H2's own body (before its first child H3) -> the H2.
  assert.equal(findOwner(heads, firstLineOf(doc, 'flat-outcome sentinel')), h2);
  assert.equal(findOwner(heads, firstLineOf(doc, 'locateChapterLine call sentinel')), h2);
  // Under the first child H3 -> that H3.
  assert.equal(findOwner(heads, firstLineOf(doc, 'grouped-only content')), grouped);
  // Under the SECOND sibling H3 -> that H3, not the first.
  assert.equal(findOwner(heads, firstLineOf(doc, 'migration content')), migration);
  assert.notEqual(findOwner(heads, firstLineOf(doc, 'migration content')), grouped);
  // After the H3s, back at H2 depth -> the next H2.
  assert.equal(findOwner(heads, firstLineOf(doc, 'glossary content')), glossary);
});

// ---------------------------------------------------------------------------------------------
// The 6 real branch-ownership pins against the shipped static-md.md — the authoritative structural
// proof underneath the shell suite's `assert_line_before` heuristic floor. Each pin proves its
// witness sits in the "## Index wiring" H2's OWN body, not under the nested grouped H3, via a
// findOwner resolution guarded by TWO uniqueness checks (sentinel unique, target H2 unique) so a
// moved real occurrence can never hide behind a correct-position decoy.
// ---------------------------------------------------------------------------------------------

const STATIC_MD = readFileSync(STATIC_MD_PATH, 'utf8');
const INDEX_WIRING_H2 = '## Index wiring (do this on every chapter create/update)';

// The 6 witnesses the bash `assert_line_before` calls pin (reference-assets.test.sh ~1630-1686) —
// each proven to sit before the grouped-only H3. Structurally, "before the grouped H3" means "in
// the H2's own body", i.e. findOwner === the H2 node.
const BRANCH_WITNESSES = [
  'locateChapterLine(indexLines,',
  'expectedTarget)`',
  'appears multiple times in <index_file>',
  '**Flat entry, line present**',
  '**Flat entry, line absent**',
  "step 0's own target.",
];

function assertBranchOwnership(text, sentinel, headingRaw) {
  // Guard 1: the sentinel occurs exactly once — otherwise findOwner on "the" occurrence is
  // meaningless (this is the guard the mutant fixture below proves catches a decoy+move).
  assert.equal(
    lineCountContaining(text, sentinel),
    1,
    `sentinel must occur exactly once, found ${lineCountContaining(text, sentinel)}: ${sentinel}`,
  );
  // Guard 2: the target H2 occurs exactly once — a duplicated heading would let findOwner resolve
  // to the wrong (first) node, silently validating nothing (mirrors the VAULT_ROOT_HEADING_COUNT
  // pattern at reference-assets.test.sh:1333-1346).
  assert.equal(
    lineCountContaining(text, headingRaw),
    1,
    `target heading must occur exactly once: ${headingRaw}`,
  );
  const heads = parseHeadings(text);
  const target = heads.find((h) => h.raw === headingRaw);
  assert.ok(target, `target heading must parse: ${headingRaw}`);
  assert.equal(
    findOwner(heads, firstLineOf(text, sentinel)),
    target,
    `sentinel must be structurally owned by ${headingRaw}: ${sentinel}`,
  );
}

test('static-md.md: the 6 branch-ownership witnesses each resolve structurally to the "## Index wiring" H2', () => {
  for (const sentinel of BRANCH_WITNESSES) {
    assertBranchOwnership(STATIC_MD, sentinel, INDEX_WIRING_H2);
  }
});

// ---------------------------------------------------------------------------------------------
// The decoy/moved-occurrence mutant fixture — the exact hole a plain first-match findOwner would
// leave open, and the uniqueness guard that closes it. Mirrors chapter-paths.test.mjs's
// "countMatches itself is pinned — a two-binding sample counts 2, not 1" precedent.
// ---------------------------------------------------------------------------------------------

const MUTANT_SENTINEL = 'flat-entry outcome sentinel';
const MUTANT_H2 = '## Index wiring';

test('mutant fixture: a decoy at the correct position + the real occurrence moved into the nested H3 is caught by the uniqueness guard (count === 2, not 1)', () => {
  // CLEAN baseline: the sentinel occurs exactly once, in the H2's own body -> the guard passes and
  // findOwner resolves to the H2.
  const clean = ['## Index wiring', MUTANT_SENTINEL, '### Grouped index wiring', 'grouped body'].join('\n');
  assert.equal(lineCountContaining(clean, MUTANT_SENTINEL), 1);
  const cleanHeads = parseHeadings(clean);
  const cleanH2 = cleanHeads.find((h) => h.raw === MUTANT_H2);
  assert.equal(findOwner(cleanHeads, firstLineOf(clean, MUTANT_SENTINEL)), cleanH2);

  // MUTANT: a correct-position DECOY of the sentinel PLUS the real occurrence relocated under the
  // nested grouped H3. A naive first-match findOwner is FOOLED — it still resolves the FIRST
  // (decoy) occurrence to the H2 and passes:
  const mutant = ['## Index wiring', MUTANT_SENTINEL, '### Grouped index wiring', MUTANT_SENTINEL].join('\n');
  const mutantHeads = parseHeadings(mutant);
  const mutantH2 = mutantHeads.find((h) => h.raw === MUTANT_H2);
  const mutantH3 = mutantHeads.find((h) => h.raw === '### Grouped index wiring');
  assert.equal(
    findOwner(mutantHeads, firstLineOf(mutant, MUTANT_SENTINEL)),
    mutantH2,
    'first-match findOwner alone false-passes on the decoy — this is why the uniqueness guard is required',
  );
  // ...and the SECOND occurrence really did move under the H3:
  const secondIdx = mutant.indexOf(MUTANT_SENTINEL, mutant.indexOf(MUTANT_SENTINEL) + 1);
  const secondLine = mutant.slice(0, secondIdx).split('\n').length;
  assert.equal(findOwner(mutantHeads, secondLine), mutantH3);
  // The uniqueness guard is what actually catches it: the sentinel now occurs on 2 lines, not 1, so
  // `assert.equal(count, 1)` in assertBranchOwnership fails loudly ("found 2") rather than the proof
  // silently passing on the decoy.
  assert.equal(lineCountContaining(mutant, MUTANT_SENTINEL), 2);
  assert.throws(
    () => assertBranchOwnership(mutant, MUTANT_SENTINEL, MUTANT_H2),
    /sentinel must occur exactly once, found 2/,
  );
});
