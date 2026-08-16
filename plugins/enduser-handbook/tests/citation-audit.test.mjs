// Citation-direction lint tests (#258). Runs under Node's built-in runner:
// `node --test citation-audit.test.mjs` (explicit path — `node --test <dir>` gives a
// misleading MODULE_NOT_FOUND).
// The shell suite (reference-assets.test.sh) auto-discovers every tests/*.test.mjs and runs it here,
// gated on `command -v node`.
//
// Coverage mirrors the plan's PR3 Verification list:
//   - non-vacuity guard (a nonzero, exact citation total; a zero-returning scanner fails loudly)
//   - the mechanically-enforced UNRESOLVED allowlist, keyed by per-occurrence IDENTITY
//     {file, section, sectionNth, quotedText, direction, nth} — asserted EXACTLY (no superset = a new unresolved
//     citation fails; no subset = a stale entry that became resolvable fails; direction in the key =
//     a direction flip on an unresolved citation fails)
//   - uniqueness guard: no citation resolves ambiguously to 2+ same-title headings
//   - direction assertion: every resolved citation's stated above/below matches its heading's real
//     position (this is the red-before-green gate for the two live obsidian-vault.md bugs)
//   - key injectivity: no two citations in the corpus share an identity, so the allowlist can never
//     silently fold two occurrences into one entry
//   - mutation tests (#342), driven against a throwaway copy of the corpus: a doc edit that touches no
//     citation must leave the allowlist untouched, and a flipped direction word must still go red
//   - synthetic fixtures: a wrapped citation, a fenced-code decoy (must be excluded), a no-verb
//     citation (round-3 scope broadening), a same-line/same-title/opposite-direction pair and a
//     same-line/same-title/SAME-direction pair (both prove the key distinguishes two occurrences a
//     line-only key would collapse), a pure offset shift (must NOT change the key), and an ambiguous
//     duplicate-heading fixture.
//
// The pinned values below are content-fragile BY DESIGN — but only where the content is a citation.
// A failure here means a citation was added, removed, re-sectioned, or flipped in the reference docs
// and must be re-reviewed; edits that move text around without touching a citation no longer register
// (#342 — see the EXPECTED_UNRESOLVED comment for the key, and the mutation tests for the proof).
// Re-derive against source, never from a stale number, and regenerate the allowlist rather than
// hand-transcribing it (run from tests/):
//   node -e "import('./citation-audit-lib.mjs').then(m => { const r = m.auditCorpus();
//     console.log(r.length, r.filter(x=>x.status==='unresolved').length); })"
//   node --input-type=module -e "const m = await import('./citation-audit-lib.mjs');
//     console.log(m.formatUnresolvedAllowlist(m.auditCorpus()));"

import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  PREAMBLE_SECTION,
  SKILL_ROOT,
  auditCorpus,
  auditText,
  citationKey,
  corpusFiles,
  extractCitations,
  formatUnresolvedAllowlist,
} from './citation-audit-lib.mjs';

// Total citation occurrences across references/**/*.md + SKILL.md (resolved + unresolved). Re-derived
// against source 2026-07-27 after the writer-prose truth pass removed one redundant cross-reference
// and the present-halt recovery correction shifted the touched adapters' offsets; pinned to the
// fresh measurement, never a number quoted in a plan. The retired occurrence was a RESOLVED one:
// obsidian-vault.md's "Nested-list automation limits" (below), which pointed at a heading it really
// did resolve to. That is why the unresolved allowlist below is unchanged at 42 entries while the
// total drops by one: a near-miss would have
// had to leave this list too. Read the two numbers together whenever this pin moves; a total that
// moves alone is the only shape consistent with a resolved citation being the one that went.
const EXPECTED_TOTAL_CITATIONS = 92;

// Every citation whose quoted text does NOT resolve to exactly one heading title in its own file — an
// over-match, a near-miss (e.g. "INDEX wiring" vs the full parenthetical heading), or a title that
// simply is not a heading ("Coordinate systems"), plus the one heading whose literal double-quotes
// (`What "Obsidian vault" implies`) cannot be cited inside a "…"-delimited citation.
//
// KEYED ON IDENTITY, NOT POSITION (#342). Each entry is
// {file, section, sectionNth, quotedText, direction, nth} — the citation's enclosing section (its
// title AND which section of that title, since one file may hold two sections named the same), its
// own quoted title and direction, and its ordinal among the citations in that section repeating that
// same title. It used to be keyed on the absolute character offset instead, which made this table a
// function of every byte before it: a typo fix or an inserted paragraph anywhere earlier in
// obsidian-vault.md or static-md.md re-wrote ~40 of these lines while changing zero citations, and a
// genuinely new or flipped citation arrived indistinguishable from that noise. This key moves only
// when something about the citation itself moves, so a diff here is signal. The mutation tests below
// assert both halves of that claim directly: a benign doc edit leaves this table untouched, and a
// flipped direction still goes red.
//
// The offset key was chosen for one specific reason — two citations of the same title on the SAME
// line with OPPOSITE directions must not collapse into one entry — and that requirement is preserved,
// not traded away. It needs BOTH halves of the ordinal design to hold, and each has its own fixture:
// `nth` separates even a same-line, same-title, SAME-direction pair (which the old
// {file, line, quotedText, direction} candidate could not), and `nth` is assigned per (section,
// title) WITHOUT direction, so swapping two opposite-direction citations changes the sorted key SET
// rather than merely reordering it — the assertions here compare sets, and a swap that leaves the set
// equal is invisible to them. `citationKey` is injective by construction and a corpus-wide test below
// asserts it stays so.
//
// One boundary is inherited rather than chosen: md-structure's parseHeadings recognizes ATX headings
// only, so a setext-underlined heading is invisible to the whole lint — its citations attribute to
// the previous ATX section. The corpus is ATX throughout; this is stated so the next reader does not
// mistake it for a property of the key.
//
// TO REGENERATE after a real citation change (run from tests/, paste between the brackets below):
//   node --input-type=module -e "const m = await import('./citation-audit-lib.mjs');
//     console.log(m.formatUnresolvedAllowlist(m.auditCorpus()));"
// A test below asserts the shipped block is character-identical to what that command emits, so a
// hand-edited entry that the regenerator would not produce fails loudly instead of drifting.
// Regeneration is NOT review: a new entry still has to be read against the source, and its DIRECTION
// verified by hand, because an unresolved citation is also a direction-unchecked one — the lint
// cannot compare against a heading it never found.
//
// The list is not homogeneous, and the shapes are worth naming (the table is the enumeration; a count
// in prose could only disagree with it):
//   * the near-miss — a heading cited by a shortened title whose real heading carries a parenthetical
//     ("INDEX wiring", "Grouped index wiring");
//   * a BULLET label, not a heading (static-md.md's "Grouped entry, line present, `indexForm:
//     'headings'`") — the reference points at something real and is more precise for a reader than
//     citing its enclosing heading would be; recorded rather than reworded so the limitation sits
//     with the lint, where it belongs, instead of bending the prose to it;
//   * the opening words of a PROSE PARAGRAPH rather than any label (obsidian-vault.md's "Path mode
//     scans") — weaker than the bullet-label shape, tolerable only because the paragraph it names is
//     stable and directly above;
//   * a BOLDED SENTENCE used as a paragraph lead-in (static-md.md's "The headings branch is unchanged
//     by this PR and already completes silently"), which reads as a heading to a human and as nothing
//     at all to this lint.
const EXPECTED_UNRESOLVED = [
  { file: 'references/diataxis.md', section: 'How the skill picks quadrants per project', sectionNth: 0, quotedText: 'When this is the right shape', direction: 'below', nth: 0 },
  { file: 'references/profile-validation.md', section: 'Structural validation against `assets/profile.schema.json`', sectionNth: 0, quotedText: '`inline` stays minimal', direction: 'below', nth: 0 },
  { file: 'references/profile-validation.md', section: 'Step 0 — ordered checks', sectionNth: 0, quotedText: 'Cross-line structural validation', direction: 'above', nth: 0 },
  { file: 'references/publish-targets/obsidian-vault.md', section: 'Vault root', sectionNth: 0, quotedText: 'Coordinate systems', direction: 'below', nth: 0 },
  { file: 'references/publish-targets/obsidian-vault.md', section: 'Vault root', sectionNth: 0, quotedText: 'Coordinate systems', direction: 'below', nth: 1 },
  { file: 'references/publish-targets/obsidian-vault.md', section: 'Vault root', sectionNth: 0, quotedText: 'INDEX wiring', direction: 'below', nth: 0 },
  { file: 'references/publish-targets/obsidian-vault.md', section: 'Vault root', sectionNth: 0, quotedText: 'What \'Obsidian vault\' implies', direction: 'above', nth: 0 },
  { file: 'references/publish-targets/obsidian-vault.md', section: 'Vault root', sectionNth: 0, quotedText: 'INDEX wiring', direction: 'below', nth: 1 },
  { file: 'references/publish-targets/obsidian-vault.md', section: 'Layout you produce', sectionNth: 0, quotedText: 'Chapter structure', direction: 'below', nth: 0 },
  { file: 'references/publish-targets/obsidian-vault.md', section: 'INDEX wiring (do all of these on every chapter create/update)', sectionNth: 0, quotedText: 'Non-headings index, no existing line', direction: 'below', nth: 0 },
  { file: 'references/publish-targets/obsidian-vault.md', section: 'INDEX wiring (do all of these on every chapter create/update)', sectionNth: 0, quotedText: 'Path mode scans', direction: 'above', nth: 0 },
  { file: 'references/publish-targets/obsidian-vault.md', section: 'INDEX wiring (do all of these on every chapter create/update)', sectionNth: 0, quotedText: 'Non-headings index', direction: 'below', nth: 0 },
  { file: 'references/publish-targets/obsidian-vault.md', section: 'INDEX wiring (do all of these on every chapter create/update)', sectionNth: 0, quotedText: 'The placement check is retained unchanged (D-8)', direction: 'above', nth: 0 },
  { file: 'references/publish-targets/obsidian-vault.md', section: 'INDEX wiring (do all of these on every chapter create/update)', sectionNth: 0, quotedText: 'Measured, across every placement', direction: 'below', nth: 0 },
  { file: 'references/publish-targets/obsidian-vault.md', section: 'INDEX wiring (do all of these on every chapter create/update)', sectionNth: 0, quotedText: 'Non-headings index, no existing line', direction: 'above', nth: 1 },
  { file: 'references/publish-targets/obsidian-vault.md', section: 'INDEX wiring (do all of these on every chapter create/update)', sectionNth: 0, quotedText: 'Container resolution', direction: 'above', nth: 0 },
  { file: 'references/publish-targets/obsidian-vault.md', section: 'Nested-list automation limits', sectionNth: 0, quotedText: 'INDEX wiring', direction: 'above', nth: 0 },
  { file: 'references/publish-targets/obsidian-vault.md', section: 'Nested-list automation limits', sectionNth: 0, quotedText: 'Non-headings index, no existing line', direction: 'above', nth: 0 },
  { file: 'references/publish-targets/obsidian-vault.md', section: 'Nested-list automation limits', sectionNth: 0, quotedText: 'Measured, across every placement', direction: 'above', nth: 0 },
  { file: 'references/publish-targets/obsidian-vault.md', section: 'Nested-list automation limits', sectionNth: 0, quotedText: 'Non-headings index, no existing line', direction: 'above', nth: 1 },
  { file: 'references/publish-targets/obsidian-vault.md', section: 'Nested-list automation limits', sectionNth: 0, quotedText: 'INDEX wiring', direction: 'above', nth: 1 },
  { file: 'references/publish-targets/obsidian-vault.md', section: 'Wikilinks vs Markdown links', sectionNth: 0, quotedText: 'INDEX wiring', direction: 'above', nth: 0 },
  { file: 'references/publish-targets/obsidian-vault.md', section: 'Link integrity gate before you publish', sectionNth: 0, quotedText: 'INDEX wiring', direction: 'above', nth: 0 },
  { file: 'references/publish-targets/static-md.md', section: 'Relative links — the general rule', sectionNth: 0, quotedText: 'Chapter path', direction: 'above', nth: 0 },
  { file: 'references/publish-targets/static-md.md', section: 'Index wiring (do this on every chapter create/update)', sectionNth: 0, quotedText: 'Grouped index wiring', direction: 'below', nth: 0 },
  { file: 'references/publish-targets/static-md.md', section: 'Index wiring (do this on every chapter create/update)', sectionNth: 0, quotedText: 'Chapter → index', direction: 'above', nth: 0 },
  { file: 'references/publish-targets/static-md.md', section: 'Index wiring (do this on every chapter create/update)', sectionNth: 0, quotedText: 'Grouped index wiring', direction: 'below', nth: 1 },
  { file: 'references/publish-targets/static-md.md', section: 'Index wiring (do this on every chapter create/update)', sectionNth: 0, quotedText: 'Grouped index wiring', direction: 'below', nth: 2 },
  { file: 'references/publish-targets/static-md.md', section: 'Grouped index wiring (`anyGroup` manifests only)', sectionNth: 0, quotedText: 'After either halt', direction: 'below', nth: 0 },
  { file: 'references/publish-targets/static-md.md', section: 'Grouped index wiring (`anyGroup` manifests only)', sectionNth: 0, quotedText: 'Grouped index wiring', direction: 'above', nth: 0 },
  { file: 'references/publish-targets/static-md.md', section: 'Grouped index wiring (`anyGroup` manifests only)', sectionNth: 0, quotedText: 'The plain-label predicate, named exactly', direction: 'below', nth: 0 },
  { file: 'references/publish-targets/static-md.md', section: 'Grouped index wiring (`anyGroup` manifests only)', sectionNth: 0, quotedText: 'Grouped entry, line present, `indexForm: \'headings\'`', direction: 'above', nth: 0 },
  { file: 'references/publish-targets/static-md.md', section: 'Nested-list automation limits', sectionNth: 0, quotedText: 'The plain-label predicate, named exactly', direction: 'below', nth: 0 },
  { file: 'references/publish-targets/static-md.md', section: 'Nested-list automation limits', sectionNth: 0, quotedText: 'The plain-label predicate, named exactly', direction: 'below', nth: 1 },
  { file: 'references/publish-targets/static-md.md', section: 'Nested-list automation limits', sectionNth: 0, quotedText: 'Grouped index wiring', direction: 'above', nth: 0 },
  { file: 'references/publish-targets/static-md.md', section: 'Nested-list automation limits', sectionNth: 0, quotedText: 'After either halt', direction: 'above', nth: 0 },
  { file: 'references/publish-targets/static-md.md', section: 'Nested-list automation limits', sectionNth: 0, quotedText: 'Grouped index wiring', direction: 'above', nth: 1 },
  { file: 'references/publish-targets/static-md.md', section: 'Nested-list automation limits', sectionNth: 0, quotedText: 'Grouped index wiring', direction: 'above', nth: 2 },
  { file: 'references/publish-targets/static-md.md', section: 'Link-integrity gate before you publish', sectionNth: 0, quotedText: 'Grouped index wiring', direction: 'above', nth: 0 },
];

const CORPUS = auditCorpus();

// The comparison every allowlist assertion makes: the sorted identities of an audit's unresolved
// citations. `citationKey` is the lib's own function, so a pinned entry and a live record can only
// agree for the right reason.
const unresolvedKeys = (records) => records.filter((r) => r.status === 'unresolved').map(citationKey).sort();
const EXPECTED_KEYS = EXPECTED_UNRESOLVED.map(citationKey).sort();

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
  // Exact set equality in both directions: a NEW unresolved citation (superset) or a stale entry that
  // became resolvable (subset) both fail here, as does a direction flip on an already-unresolved one.
  assert.deepEqual(
    unresolvedKeys(CORPUS),
    EXPECTED_KEYS,
    'unresolved citation set drifted from the pinned allowlist — inspect the diff; a new entry needs review, a vanished one needs cleanup',
  );
});

test('key injectivity: no two citations in the corpus share an identity (#342)', () => {
  // The property the allowlist rests on. An offset was unique for free; an identity is unique only
  // because `nth` counts repeats within its group, so this is asserted rather than assumed — a
  // regression that dropped the ordinal would otherwise show up as a silently SHORTER allowlist
  // (two occurrences folding into one entry) instead of a failure.
  const byKey = new Map();
  for (const r of CORPUS) {
    const k = citationKey(r);
    if (!byKey.has(k)) byKey.set(k, []);
    byKey.get(k).push(`${r.file}:${r.line}@${r.offset}`);
  }
  const collisions = [...byKey.entries()]
    .filter(([, where]) => where.length > 1)
    .map(([k, where]) => `${k.replace(/\0/g, ' | ')} -> ${where.join(', ')}`);
  assert.deepEqual(collisions, [], `citation identities collided:\n${collisions.join('\n')}`);
  assert.equal(byKey.size, CORPUS.length, 'every citation must have its own identity');
});

test('the pinned allowlist is exactly what the regeneration command emits (#342)', () => {
  // Closes the loop the issue asks for: the documented regenerate-and-paste command is the ONLY way
  // this table is meant to change, so its output must equal the block this file ships. A hand-edited
  // entry, a lost line, a re-sorted table or an escaping bug in the emitter all fail here.
  const source = readFileSync(fileURLToPath(import.meta.url), 'utf8');
  const OPEN = 'const EXPECTED_UNRESOLVED = [\n';
  const start = source.indexOf(OPEN);
  assert.ok(start !== -1, 'EXPECTED_UNRESOLVED array not found in this file');
  const end = source.indexOf('\n];', start);
  assert.ok(end !== -1, 'EXPECTED_UNRESOLVED array is not terminated');
  const shipped = source.slice(start + OPEN.length, end);
  assert.equal(
    shipped,
    formatUnresolvedAllowlist(CORPUS),
    'the pinned block differs from the regenerated one — re-run the regeneration command in the ' +
      'EXPECTED_UNRESOLVED comment and paste its output, then review each changed entry',
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
// Mutation tests (#342) — both directions, against a THROWAWAY COPY of the real corpus.
//
// The allowlist above is only worth its cost if it stays silent on doc edits that touch no citation
// and speaks up when a citation actually goes wrong. Neither half is provable from the live tree
// alone (it is, by construction, the state where both hold today), so both are driven here against a
// materialized copy of the corpus with one targeted edit applied.
// ---------------------------------------------------------------------------------------------

// Write every corpus file into a fresh temp root, passing each through `mutate(file, text)`, then run
// `fn(root)` against it. The root is a directory `auditCorpus(root)` accepts: references/**/*.md plus
// SKILL.md, nothing else. Materialization happens INSIDE the try, so a mutate callback that throws
// mid-write cannot leave the copy behind — same withTempDir shape tests/capture-record.test.mjs uses.
function withMaterializedCorpus(mutate, fn) {
  const root = mkdtempSync(join(tmpdir(), 'citation-audit-'));
  try {
    for (const file of corpusFiles()) {
      const dest = join(root, file);
      mkdirSync(dirname(dest), { recursive: true });
      writeFileSync(dest, mutate(file, readFileSync(join(SKILL_ROOT, file), 'utf8')));
    }
    return fn(root);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

// The two files #342 is about — every citation in the corpus's two most-edited docs sits below this
// insertion point, so a single insertion moves every one of their offsets at once.
const MOST_EDITED = ['references/publish-targets/obsidian-vault.md', 'references/publish-targets/static-md.md'];

// A paragraph of ordinary prose: no heading, no quoted title, no direction word — nothing this lint
// models. Inserted immediately after the file's `# ` title, i.e. above everything else in the file.
const BENIGN_PARAGRAPH = [
  '',
  'Inserted by the #342 benign-edit mutation test. This paragraph introduces no heading, quotes no',
  'title and states no direction, so it changes nothing the citation lint models — it only moves the',
  'character offset of every citation below it.',
].join('\n');

function insertBenignParagraph(file, text) {
  if (!MOST_EDITED.includes(file)) return text;
  const firstBreak = text.indexOf('\n');
  assert.ok(firstBreak > 0, `${file}: expected a multi-line document`);
  return `${text.slice(0, firstBreak)}\n${BENIGN_PARAGRAPH}${text.slice(firstBreak)}`;
}

test('mutation: a doc edit that touches no citation leaves the pinned allowlist green (#342)', () => {
  withMaterializedCorpus(insertBenignParagraph, (root) => {
    const mutated = auditCorpus(root);
    // Length first: the offset comparison below pairs the two audits BY INDEX, which is only a
    // like-for-like comparison while both hold the same citations in the same document order.
    assert.equal(mutated.length, CORPUS.length, 'a benign edit must not change the citation count');
    assert.equal(mutated.length, EXPECTED_TOTAL_CITATIONS, 'a benign edit must not change the citation total');
    // Non-vacuity of the mutation itself: the edit really did move offsets, so a green result below
    // is a property of the KEY, not of an edit that quietly did nothing.
    const movedOffsets = mutated.filter((r, i) => r.offset !== CORPUS[i].offset).length;
    assert.ok(
      movedOffsets > 0,
      'the benign edit moved ZERO offsets — the mutation is a no-op and proves nothing',
    );
    assert.deepEqual(
      unresolvedKeys(mutated),
      EXPECTED_KEYS,
      `a doc edit touching no citation must not disturb the allowlist (it moved ${movedOffsets} offsets)`,
    );
  });
});

// The same separator grammar extractCitations requires between a citation's last quote and its
// direction word — whitespace, a single [,;:], or the word "and" — anchored, so it matches right
// there or not at all.
const DIRECTION_RIGHT_AFTER_QUOTE = /^(?:[\s,;:]|\band\b)*(above|below)\b/i;

// Where `rec`'s OWN direction word starts, or -1 when no direction word sits immediately after this
// record's closing quote. A compound `"A" and "B" below` chain carries ONE direction word, after its
// LAST quote, so a non-final member of a chain returns -1 here rather than a forward-scanned guess —
// which could otherwise land inside a later quoted title that happens to contain the word "above" or
// "below" and mutate the wrong text. Anchoring is what makes the answer exact instead of heuristic.
function directionWordOffset(text, rec) {
  const afterQuote = rec.offset + rec.quotedRaw.length + 2; // +2 = the title's own enclosing quote pair
  const m = DIRECTION_RIGHT_AFTER_QUOTE.exec(text.slice(afterQuote));
  return m === null ? -1 : afterQuote + m[0].length - m[1].length;
}

function flipDirectionAt(text, at, direction) {
  const found = text.slice(at, at + direction.length).toLowerCase();
  assert.equal(found, direction, `expected "${direction}" at offset ${at}, found "${found}" — offset math is wrong`);
  return text.slice(0, at) + (direction === 'above' ? 'below' : 'above') + text.slice(at + direction.length);
}

test('mutation: a genuinely wrong-direction citation still goes RED (#342)', () => {
  // The FIRST resolved, correctly-directed citation that owns its direction word — chosen positionally
  // and by structure, never by its prose, so this test does not re-acquire the doc-text coupling #342
  // removes.
  const sources = new Map();
  const textFor = (file) => {
    if (!sources.has(file)) sources.set(file, readFileSync(join(SKILL_ROOT, file), 'utf8'));
    return sources.get(file);
  };
  let victim = null;
  let victimAt = -1;
  for (const r of CORPUS) {
    if (r.status !== 'resolved' || !r.directionOk || r.expectedDirection === 'same') continue;
    const at = directionWordOffset(textFor(r.file), r);
    if (at !== -1) {
      victim = r;
      victimAt = at;
      break;
    }
  }
  assert.ok(victim, 'corpus has no correctly-directed resolved citation owning its own direction word');
  withMaterializedCorpus(
    (file, text) => (file === victim.file ? flipDirectionAt(text, victimAt, victim.direction) : text),
    (root) => {
      const mutated = auditCorpus(root);
      assert.equal(mutated.length, EXPECTED_TOTAL_CITATIONS, 'flipping a direction word must not change the total');
      const wrong = mutated.filter((r) => r.status === 'resolved' && !r.directionOk);
      assert.ok(
        wrong.some((r) => r.file === victim.file && r.quotedText === victim.quotedText),
        `flipping "${victim.quotedText}" in ${victim.file} produced NO wrong-direction finding — the lint is blind`,
      );
      // And the allowlist stays quiet: a direction flip on a RESOLVED citation is caught by the
      // direction assertion, not by the unresolved set, so the two guards do not mask each other.
      assert.deepEqual(
        unresolvedKeys(mutated),
        EXPECTED_KEYS,
        'a direction flip on a resolved citation must not move the unresolved allowlist',
      );
    },
  );
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

test('synthetic: same-line, same-title, opposite-direction pair stays distinguishable (allowlist key)', () => {
  // Mirrors codex round-3: a {file,line,quotedText,direction} key cannot tell these two apart on a
  // swap. "Missing" is not a heading, so both are unresolved.
  const text = 'For A, see "Missing" above; for B, see "Missing" below.';
  const recs = auditText(text);
  assert.equal(recs.length, 2, 'two citations on one line');
  assert.deepEqual(recs.map((r) => r.quotedText), ['Missing', 'Missing'], 'same quoted title');
  assert.deepEqual(recs.map((r) => r.direction).sort(), ['above', 'below'], 'opposite directions');
  assert.deepEqual(recs.map((r) => r.status), ['unresolved', 'unresolved']);
  assert.equal(new Set(recs.map((r) => r.line)).size, 1, 'both on the SAME line — a line-only key would collapse them');
  assert.equal(new Set(recs.map((r) => r.offset)).size, 2, 'distinct offsets — the position layer still separates them');
  assert.equal(new Set(recs.map(citationKey)).size, 2, 'and distinct identities — direction alone already separates this pair');
});

test('synthetic: same-line, same-title, SAME-direction pair is separated by the ordinal (#342)', () => {
  // The hardest case for a position-free key, and the one direction cannot break: two citations that
  // agree on file, section, title AND direction. `nth` is what keeps them two entries instead of one.
  const text = 'Both "Missing" below and, later on the same line, "Missing" below.';
  const recs = auditText(text);
  assert.equal(recs.length, 2, 'two citations on one line');
  assert.deepEqual(recs.map((r) => r.direction), ['below', 'below'], 'SAME direction — no help from that field');
  assert.equal(new Set(recs.map((r) => r.line)).size, 1, 'both on the SAME line');
  assert.deepEqual(recs.map((r) => r.nth), [0, 1], 'ordinals assigned in document order');
  assert.equal(new Set(recs.map(citationKey)).size, 2, 'two distinct identities — the pair does NOT collapse');
});

test('synthetic: text inserted above a citation shifts its offset but NOT its identity (#342)', () => {
  // The whole point of the re-key, at unit scale: identical document, one paragraph of unrelated
  // prose inserted above. The offset moves; file/section/title/direction/ordinal do not.
  const body = ['## Section one', '', 'see "Section one" above'].join('\n');
  const before = auditText(body);
  const after = auditText(`${['## Section one', '', 'Unrelated prose that cites nothing at all.', ''].join('\n')}\n${body.split('\n').slice(1).join('\n')}`);
  assert.equal(before.length, 1);
  assert.equal(after.length, 1);
  assert.notEqual(after[0].offset, before[0].offset, 'the insertion really did move the citation');
  assert.notEqual(after[0].line, before[0].line, 'and moved its line');
  assert.equal(after[0].status, 'resolved');
  assert.equal(after[0].directionOk, true, 'and left the direction correct');
  assert.equal(citationKey(after[0]), citationKey(before[0]), 'identity is unchanged by a pure position shift');
});

test('synthetic: the flip harness targets the direction word a citation OWNS, never a later one (#342)', () => {
  // A compound chain carries ONE direction word, after its LAST quote — and here a decoy copy of that
  // word sits inside the second title. An unanchored forward scan from the first record would find the
  // decoy and mutate the wrong text, producing a doc whose citations are all still correctly directed
  // and a wrong-direction test that fails for a reason having nothing to do with the lint.
  const text = 'Compound "A" and "B below" below.';
  const recs = extractCitations(text);
  assert.equal(recs.length, 2, 'both titles in the chain are extracted');
  assert.deepEqual(recs.map((r) => r.quotedText), ['A', 'B below'], 'the decoy word sits inside the second title');
  assert.equal(directionWordOffset(text, recs[0]), -1, 'a non-final chain member owns no direction word');
  const at = directionWordOffset(text, recs[1]);
  assert.equal(at, text.lastIndexOf('below'), 'the final member owns the TRAILING word, not the one inside a title');
  assert.equal(flipDirectionAt(text, at, 'below'), 'Compound "A" and "B below" above.', 'only that word flips');
});

test('synthetic: the section boundary answers, pinned rather than left to be rediscovered (#342)', () => {
  // Both come from md-structure's findOwner, which this lint reuses instead of re-deriving "which
  // section is this line in". Zero corpus citations hit either case today, so without a fixture the
  // key's behaviour at its own boundaries would be undocumented and free to drift.
  const preamble = auditText(['see "Later" below', '', '## Later', '', 'body'].join('\n'));
  assert.equal(preamble.length, 1);
  assert.equal(preamble[0].section, PREAMBLE_SECTION, 'a citation before the first heading has no owning section');
  assert.equal(preamble[0].status, 'resolved');
  assert.equal(preamble[0].directionOk, true, 'and is still direction-checked normally');

  // A citation written INTO a heading line belongs to that heading's PARENT — a heading is not inside
  // its own body. The `## Prior` sibling is what makes this fixture discriminate: without it, a
  // "nearest heading strictly above" implementation returns 'Outer' too and the fixture would pass
  // coincidentally. With it, that wrong implementation returns 'Prior' while the parent rule returns
  // 'Outer'.
  const inHeading = auditText(
    ['# Outer', '', 'body', '', '## Prior', '', 'more', '', '## See "Outer" above', '', 'tail'].join('\n'),
  );
  assert.equal(inHeading.length, 1);
  assert.equal(inHeading[0].section, 'Outer', 'the enclosing section is the parent heading, not the nearest one above');
  assert.equal(inHeading[0].directionOk, true);
});

test('synthetic: two sections sharing a title do NOT alias — a citation moving between them is seen (#342)', () => {
  // Codex found this one: the section TITLE alone is not a section identity. "Bullet" is not a
  // heading, so the citation is unresolved and therefore direction-unchecked — the allowlist entry is
  // the ONLY record that it exists at all. Moving it from the first `## Same` to the second is a real
  // change of context, and with the title as the whole section component both positions produced the
  // same key: the move was invisible in the one place it could have been caught.
  const inFirst = ['## Same', 'See "Bullet" below.', 'Bullet is here.', '## Same', 'body'].join('\n');
  const inSecond = ['## Same', 'Bullet is here.', '## Same', 'See "Bullet" below.', 'body'].join('\n');
  const [a] = auditText(inFirst);
  const [b] = auditText(inSecond);
  assert.equal(a.status, 'unresolved', 'the moved citation is direction-unchecked — the key is the only guard');
  assert.equal(b.status, 'unresolved');
  assert.equal(a.section, b.section, 'same section TITLE in both positions');
  assert.deepEqual([a.sectionNth, b.sectionNth], [0, 1], 'but a different section INSTANCE');
  assert.notEqual(citationKey(a), citationKey(b), 'so the move changes the key and the allowlist goes red');

  // And the ordinal counts same-TITLED headings, not headings — otherwise it would churn the whole
  // table every time an unrelated heading was inserted, trading the offset key's noise for a quieter
  // one. This has to be asserted on a citation in the SECOND `## Same`, with a differently-titled
  // heading inserted BETWEEN the two: only there do the two readings disagree (same-title ordinal 1,
  // global heading index 2). Asserted on the first section instead, both readings say 0 and the
  // assertion passes under either — measured, not assumed: a global-index regression left an earlier
  // version of this fixture green.
  const withSibling = ['## Same', 'Bullet is here.', '## Other', 'x', '## Same', 'See "Bullet" below.', 'body'].join('\n');
  const [c] = auditText(withSibling);
  assert.equal(c.sectionNth, 1, 'still the second section of that title, not the third heading');
  assert.equal(citationKey(c), citationKey(b), 'so an unrelated heading inserted between them changes no key');
});

test('synthetic: a heading titled like the preamble sentinel does not inherit its identity (#342)', () => {
  // The sentinel is a STRING, so a document could name a real heading exactly that. Deciding the
  // preamble by absence of an owner is not enough on its own — that heading's citations would still
  // collapse the title to the sentinel and take ordinal 0. The preamble's negative ordinal is what
  // no heading can produce, and therefore what actually separates them.
  const [real] = auditText(['see "X" below', '', '## X', 'body'].join('\n'));
  const [decoy] = auditText([`# ${PREAMBLE_SECTION}`, '', 'see "X" below', '', '## X', 'body'].join('\n'));
  assert.equal(real.section, PREAMBLE_SECTION, 'a genuine preamble citation');
  assert.equal(decoy.section, PREAMBLE_SECTION, 'and one inside a heading that spells the sentinel');
  assert.notEqual(citationKey(real), citationKey(decoy), 'must not share an identity');
  assert.ok(decoy.sectionNth >= 0, 'a real heading always takes a non-negative ordinal');
});

test('synthetic: swapping two opposite-direction citations changes the key SET, not just their order (#342)', () => {
  // The frozen hard constraint, stated as the assertion the allowlist actually makes. The set test
  // compares SORTED keys, so "the two records are distinguishable" is not enough — a swap has to move
  // the set. That is why the ordinal is assigned per (section, title) and NOT per direction: with
  // direction inside the ordinal group both records keep nth 0, the swapped set is identical, and the
  // swap is invisible to every set-based assertion.
  const before = auditText(['## S', 'For A see "Missing" above; for B see "Missing" below.'].join('\n'));
  const after = auditText(['## S', 'For A see "Missing" below; for B see "Missing" above.'].join('\n'));
  assert.equal(before.length, 2);
  assert.equal(after.length, 2);
  assert.deepEqual(before.map((r) => r.nth), [0, 1], 'numbered as a pair, regardless of direction');
  assert.notDeepEqual(
    before.map(citationKey).sort(),
    after.map(citationKey).sort(),
    'a direction swap must change the key SET — this is the case the offset key was chosen for',
  );
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
  // A single timing sample cannot distinguish "slow because quadratic" from "slow because preempted",
  // so every number below is repeated. The MINIMUM was tried first and rejected: preemption only adds
  // time, so a minimum does converge on the uncontended cost — but it also discards a majority, and
  // four slow calls plus one fast one are then indistinguishable from five fast ones, which is a
  // false green for any intermittent or cold-path regression staying under the absolute bound.
  // The two assertions below read DIFFERENT statistics, on purpose, because they ask different
  // questions. The SCALING ratio wants the TYPICAL cost and takes medians at both levels — the
  // typical sample within each batch, then the median across paired ratios. The ABSOLUTE blow-up
  // bound wants the worst thing that actually happened and takes the maximum.
  // Two earlier revisions of this comment described statistics the code was not using: one said the
  // absolute bound was single-sample while `timeFor` returned only a minimum, the next still said the
  // ratio used the minimum after it had moved to medians. If this paragraph and the code below ever
  // disagree again, the code is what runs.
  const TIMING_SAMPLES = 5;
  const timeFor = (n) => {
    const decoyRun = '"a" '.repeat(n) + 'end.';
    let best = Infinity;
    let worst = 0;
    const samples = [];
    for (let i = 0; i < TIMING_SAMPLES; i += 1) {
      const start = process.hrtime.bigint();
      const recs = extractCitations(decoyRun);
      const elapsedMs = Number(process.hrtime.bigint() - start) / 1e6;
      assert.deepEqual(recs, [], `n=${n}: no trailing above/below means no citation span should match at all`);
      samples.push(elapsedMs);
      if (elapsedMs < best) best = elapsedMs;
      if (elapsedMs > worst) worst = elapsedMs;
    }
    samples.sort((a, b) => a - b);
    // TYPICAL, not fastest. The minimum discards a majority: four slow calls and one fast one look
    // identical to five fast ones, so an intermittent or cold-path superlinear regression staying
    // under the absolute bound would pass. The median tolerates up to two contaminated samples in
    // either direction, which is what the scheduling noise actually looks like, and still moves when
    // most calls are slow.
    const typical = samples[Math.floor(samples.length / 2)];
    return { best, worst, typical };
  };
  // The STATISTIC was the problem, not the input size and not the ceiling. Taking the minimum of
  // each side SEPARATELY does nothing when one side is inflated across all its samples, and the
  // noise runs both ways — measured in-suite, single paired ratios at these sizes ranged 3.2x to
  // 108.7x, the low end meaning the SMALL run was the one that got hit.
  // Two things were tried and measured before this one, and both are recorded so they are not
  // retried: enlarging both inputs to 20k/320k made the in-suite median WORSE (56.7x vs 15.8x,
  // both pairs measured in the same runs) because at 320,000 titles the working set leaves cache
  // and per-title cost rises; and raising the ceiling to 120x still failed 1 run in 20.
  // What works is medians at BOTH levels: the typical sample within each batch, and the median of
  // PAIRS paired ratios across batches. Measured in-suite over 16 full-suite runs each way, that
  // spans 12.1x-29.3x, against 18.7x-45.1x when each side was minimised instead and 3.2x-108.7x for
  // a single unrepeated pair. Minimising was also unsound in a way the noise hid: it discards a
  // majority, so four slow calls and one fast one are indistinguishable from five fast ones.
  //
  // THE CEILING IS SET FROM BOTH ENDS, BOTH MEASURED. Healthy tops out at 29.3x. The retired
  // catastrophic matcher — the actual regression this test exists to catch, recovered and RUN rather
  // than assumed — measured 117.4x/38,847ms, 228.2x/29,891ms and 256.5x/33,943ms across hosts and
  // runs, so quote it as a RANGE: it never came close to passing, and the lowest observation is the
  // one that matters for margin. 60x therefore sits 2.0x above the worst healthy sample and at least
  // 2.0x below the regression.
  // Which gate is decisive: ~30 SECONDS against a 2,000ms bound is a ~15x margin, while the ratio's
  // is 2x. The absolute bound is the gate; the ratio is the early signal, and that ordering is why
  // the ratio's narrower band is acceptable.
  // KNOWN BLIND SPOT, stated rather than implied: this catches the catastrophic matcher, not all
  // superlinearity. A mild n^1.4 curve produces ~48.5x at 16x input and passes. Tightening toward
  // that would collide with healthy noise at ~29x; closing it needs step-counting, not wall time.
  // Do not tighten toward 16x "because linear should be 16x" — measured, linear is ~20x here.
  const PAIRS = 5;
  const ratios = [];
  let worstLarge = 0;
  for (let i = 0; i < PAIRS; i += 1) {
    const s = timeFor(5_000);
    const l = timeFor(80_000); // 16x the input
    ratios.push(l.typical / s.typical);
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
    ratio < 60,
    `16x input took ${ratio.toFixed(1)}x longer (median of ${PAIRS} paired samples) — that is ` +
      `n^${(Math.log(ratio) / Math.log(16)).toFixed(2)} scaling, past the 60x safety ceiling ` +
      '(healthy measures ~n^1.22, the retired catastrophic matcher n^1.72-n^2.00); possible ReDoS ' +
      'regression, or a loaded machine — re-measure on a quiet box before touching the regex',
  );
});
