// Unit tests for the group-axis path/gate helpers (issue #19, plan D1-D6). Zero deps beyond
// node:fs/node:path/node:url for reading the real capture.example.spec.ts skeleton — runs under
// Node's built-in test runner: `node --test chapter-paths.test.mjs` (explicit path — `node --test
// <dir>` gives a misleading MODULE_NOT_FOUND).
//
// Section order mirrors the plan's "5. Tests" list: path formulas -> validateGroups ->
// locateChapterLine/findContainer -> groupChanges -> manualMigrationChecklist -> specReferencesDir
// -> chapterHasWikilinkTo -> renderManualMigrationHalt -> the consumer-binding structural pin
// against capture.example.spec.ts.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  anyGroup,
  chapterRelPath,
  chapterAssetDir,
  embedPath,
  legacyStaticEmbedPath,
  staticEmbedPath,
  validateGroups,
  indexView,
  locateChapterLine,
  leadingFrontmatterSpan,
  currentIndexExpectedTarget,
  classifyChapterWiring,
  findContainer,
  wireNestedListChapter,
  verifyNonHeadingPlacement,
  extractLabel,
  isPlainLabel,
  groupChanges,
  manualMigrationChecklist,
  renderManualMigrationHalt,
  specReferencesDir,
  chapterHasWikilinkTo,
  containerTitleMatches,
} from '../skills/enduser-handbook/assets/lib/chapter-paths.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const SPEC_PATH = join(HERE, '../skills/enduser-handbook/assets/capture.example.spec.ts');

// ---------------------------------------------------------------------------------------------
// Fixture builders
// ---------------------------------------------------------------------------------------------

function profile(overrides = {}) {
  return {
    capture: { output_dir: 'vault/handbook/assets', ...(overrides.capture ?? {}) },
    publish: {
      chapters_dir: 'vault/handbook',
      index_file: 'vault/SUMMARY.md',
      wikilinks: false,
      ...(overrides.publish ?? {}),
    },
  };
}

function entry(overrides = {}) {
  return { slug: 'items', ...overrides };
}

function findFact(facts, kind) {
  return facts.find((f) => f.kind === kind);
}

// =================================================================================================
// D2/D3 path formulas
// =================================================================================================

test('flat byte-identity: chapterAssetDir == join(output_dir, slug) for a flat entry', () => {
  assert.equal(chapterAssetDir(profile(), entry()), 'vault/handbook/assets/items');
});

test('embedPath reproduces the shipped nested worked example', () => {
  const chapterFile = 'vault/handbook/items.md';
  const assetDir = chapterAssetDir(profile(), entry());
  assert.equal(embedPath(chapterFile, assetDir, '01-overview.png'), 'assets/items/01-overview.png');
});

test('embedPath reproduces the shipped degenerate flat worked example (no leading slash)', () => {
  const degenerate = profile({ capture: { output_dir: 'vault/handbook' } });
  const chapterFile = 'vault/handbook/items.md';
  const assetDir = chapterAssetDir(degenerate, entry());
  assert.equal(embedPath(chapterFile, assetDir, '01-overview.png'), 'items/01-overview.png');
});

test('Mode-convergence pin [1.6.0, #220]: staticEmbedPath returns the SAME full-target canon regardless of anyGroup', () => {
  // Inverts the pre-1.6.0 "Degenerate mode-divergence pin" — #220 drops the anyGroup branch
  // entirely, so a group-free manifest's degenerate embed no longer keeps the leading-slash
  // legacy quirk; it converges on the exact same result an anyGroup manifest already got.
  const degenerate = profile({ capture: { output_dir: 'vault/handbook' } });
  const chapterFile = 'vault/handbook/items.md';
  const flatOnly = [entry()];
  const grouped = [entry(), entry({ slug: 'other', group: 'g', group_title: 'G' })];

  assert.equal(
    staticEmbedPath(flatOnly, chapterFile, degenerate, entry(), '01-overview.png'),
    'items/01-overview.png',
    'group-free manifest must now use the full-target formula, no leading-slash quirk',
  );
  assert.equal(
    staticEmbedPath(grouped, chapterFile, degenerate, entry(), '01-overview.png'),
    'items/01-overview.png',
    'anyGroup manifest is unaffected — still the full-target form',
  );
});

test('staticEmbedPath new-write table [1.6.0, #220]: full-target canon across all three chapter/output_dir layouts', () => {
  // F2's three-row divergence table. Only the sibling layout is byte-unchanged from 1.4.1/1.5.0;
  // the degenerate and parent layouts both CHANGE — do not assert "byte-unchanged for every
  // non-degenerate layout", that claim is false (the parent row proves it).
  const flatOnly = [entry()];
  const cases = [
    {
      label: 'sibling (output_dir strictly below chapters_dir — the common worked example)',
      profileLike: profile(),
      chapterFile: 'vault/handbook/items.md',
      legacy: 'assets/items/01.png',
      canon: 'assets/items/01.png', // SAME
    },
    {
      label: 'degenerate (chapter dir === output_dir)',
      profileLike: profile({ capture: { output_dir: 'vault/handbook' } }),
      chapterFile: 'vault/handbook/items.md',
      legacy: '/items/01.png',
      canon: 'items/01.png', // CHANGES
    },
    {
      label: 'parent (output_dir strictly above chapters_dir)',
      profileLike: profile({
        capture: { output_dir: 'vault/handbook' },
        publish: { chapters_dir: 'vault/handbook/items' },
      }),
      chapterFile: 'vault/handbook/items/items.md',
      legacy: '../items/01.png',
      canon: '01.png', // CHANGES
    },
  ];
  for (const { label, profileLike, chapterFile, legacy, canon } of cases) {
    assert.equal(
      legacyStaticEmbedPath(chapterFile, profileLike.capture.output_dir, entry().slug, '01.png'),
      legacy,
      `${label}: legacyStaticEmbedPath (retained spelling) must be unchanged`,
    );
    assert.equal(
      staticEmbedPath(flatOnly, chapterFile, profileLike, entry(), '01.png'),
      canon,
      `${label}: staticEmbedPath always writes the full-target canon now`,
    );
  }
});

test('legacyStaticEmbedPath: slug and file pin [round-13 audit]: each param is genuinely consulted, not hardcoded', () => {
  // Round-13 audit finding: `legacyStaticEmbedPath` has exactly two call sites in this whole
  // file, and both pass `entry().slug` ('items') and the literal '01.png' — `slug` and `file`
  // never vary. A mutant hardcoding either inside the function body (e.g. always 'items', or
  // always '01.png') would pass every existing assertion unchanged. Retained-but-uncalled by
  // design (#220 dropped staticEmbedPath's call to it) — it stays exported as the reference
  // spelling the deferred #246 repair engine will read, so a silent bug here would be inherited
  // by that future work. Vary slug and file ONE AT A TIME, each against the other held at its
  // usual constant, so each parameter independently proves it is not a hardcoded literal.
  const chapterFile1 = 'vault/handbook/orders.md';
  assert.equal(
    legacyStaticEmbedPath(chapterFile1, 'vault/handbook/assets', 'orders', '01.png'),
    'assets/orders/01.png',
    'slug alone must select the resulting path — not silently "items"',
  );
  const chapterFile2 = 'vault/handbook/items.md';
  assert.equal(
    legacyStaticEmbedPath(chapterFile2, 'vault/handbook/assets', 'items', 'diagram.svg'),
    'assets/items/diagram.svg',
    'file alone must select the resulting path — not silently "01.png"',
  );
});

test('staticEmbedPath positional-argument pin [round-10]: uses the CURRENT entry, not entries[0]', () => {
  // Round-10 finding: every prior staticEmbedPath test passed `entry()` as BOTH the `entries`
  // array's sole/first member AND the standalone `entry` argument, so `entry` and `entries[0]`
  // were always the same object — a mutant that swaps `entry` for `entries[0]` inside the
  // function stayed fully green. `entries = [intro, admin/items]` with `admin/items` (index 1,
  // NOT entries[0]) as the current entry exercises the argument that actually selects the asset
  // directory.
  const p = profile();
  const entries = [entry({ slug: 'intro' }), entry({ slug: 'items', group: 'admin', group_title: 'Admin' })];
  const current = entries[1];
  const chapterFile = join(p.publish.chapters_dir, chapterRelPath(current));
  assert.equal(
    staticEmbedPath(entries, chapterFile, p, current, '01.png'),
    '../assets/admin/items/01.png',
    "must derive the CURRENT entry's ('admin/items') asset dir",
  );
  assert.notEqual(
    staticEmbedPath(entries, chapterFile, p, current, '01.png'),
    '../assets/intro/01.png',
    "must not silently resolve to entries[0]'s ('intro') asset dir",
  );
});

test('staticEmbedPath positional-argument family-kill [round-10]: current entry is neither first nor last', () => {
  // The single two-entry case above only rules out `entries[0]` — in a 2-entry array `entries[1]`
  // IS `entries[entries.length - 1]`, so a mutant swapping `entry` for the LAST entry instead of
  // the first would still pass it undetected. A 3-entry manifest with the current entry in the
  // MIDDLE (neither index 0 nor index length-1) kills that whole family of positional-pick
  // mutants at once.
  const p = profile();
  const entries = [
    entry({ slug: 'first', group: 'a', group_title: 'A' }),
    entry({ slug: 'second', group: 'b', group_title: 'B' }),
    entry({ slug: 'third', group: 'c', group_title: 'C' }),
  ];
  const current = entries[1];
  const chapterFile = join(p.publish.chapters_dir, chapterRelPath(current));
  assert.equal(staticEmbedPath(entries, chapterFile, p, current, '01.png'), '../assets/b/second/01.png');
});

test('staticEmbedPath chapterFile pin [round-11]: a capture-only profileLike (no `publish` key) is honored, never thrown on', () => {
  // Round-11 finding: every prior fixture supplied a full profile AND a chapterFile derivable
  // from that profile plus the entry, so a mutant that ignores the chapterFile argument and
  // silently recomputes it as `profileLike.publish.chapters_dir + chapterRelPath(entry)` stayed
  // green. `chapter-paths.d.mts:16` states profileLike is the CAPTURE-ONLY subset staticEmbedPath
  // actually reads at runtime (never `publish`) — a real capture spec legitimately never
  // constructs `publish.*`. A capture-only profileLike (literally no `publish` key) with a
  // chapterFile deliberately off the chapters_dir tree entirely (there is no chapters_dir to be
  // on) proves both halves of the contract at once: the mutant would THROW reading
  // `profileLike.publish.chapters_dir` off `undefined`, while the real helper never touches
  // `publish` and correctly derives the answer from the given chapterFile.
  const captureOnly = { capture: { output_dir: 'vault/handbook/assets' } };
  const chapterFile = 'somewhere/else/chapter.md'; // unrelated to output_dir; no chapters_dir exists to derive it from
  assert.equal(
    staticEmbedPath([entry()], chapterFile, captureOnly, entry(), '01.png'),
    '../../vault/handbook/assets/items/01.png',
  );
});

test('staticEmbedPath chapterFile pin [round-11]: an off-tree chapterFile is honored even when publish.chapters_dir IS present', () => {
  // Companion to the capture-only case above: here `publish.chapters_dir` exists, so the
  // ignore-chapterFile mutant would NOT throw — it would silently recompute a wrong chapterFile
  // from the profile and entry instead, and mis-resolve rather than error. chapterFile is chosen
  // to sit off the chapters_dir tree entirely (not `chapters_dir + chapterRelPath(entry)`) so a
  // real vs. recomputed chapterFile produce PROVABLY DIFFERENT results, catching the mutation by
  // wrong-value rather than by throw.
  const p = profile(); // publish.chapters_dir = 'vault/handbook'
  const chapterFile = 'somewhere-else/chapter.md'; // deliberately NOT chapters_dir + chapterRelPath(entry)
  assert.equal(
    staticEmbedPath([entry()], chapterFile, p, entry(), '01.png'),
    '../vault/handbook/assets/items/01.png',
  );
});

test('chapterRelPath: flat and grouped forms', () => {
  assert.equal(chapterRelPath(entry()), 'items.md');
  assert.equal(chapterRelPath(entry({ group: 'admin', group_title: 'Admin' })), 'admin/items.md');
});

test('D3 three rows: chapterAssetDir is activation-independent (same formula flat/anyGroup/grouped)', () => {
  const p = profile();
  // group-free / any entry.
  assert.equal(chapterAssetDir(p, entry()), 'vault/handbook/assets/items');
  // anyGroup / flat entry — identical formula, identical result.
  assert.equal(chapterAssetDir(p, entry()), 'vault/handbook/assets/items');
  // anyGroup / grouped entry.
  assert.equal(
    chapterAssetDir(p, entry({ group: 'admin', group_title: 'Admin' })),
    'vault/handbook/assets/admin/items',
  );
});

test('grouped embed climbs correctly for a worked grouped example (exact string)', () => {
  const p = profile();
  const groupedEntry = entry({ group: 'admin', group_title: 'Admin' });
  const chapterFile = join(p.publish.chapters_dir, chapterRelPath(groupedEntry));
  const assetDir = chapterAssetDir(p, groupedEntry);
  assert.equal(embedPath(chapterFile, assetDir, '01-overview.png'), '../assets/admin/items/01-overview.png');
});

test('R10-F1 end-to-end degenerate divergence: full-target formula resolves where the legacy formula loops forever', () => {
  // A grouped->flat move landing on the degenerate layout (chapter dir === output_dir): the
  // legacy partial-concatenation spelling keeps the leading-slash quirk forever, but the recipe's
  // full-target rewrite converges — this is why the recipe ALWAYS uses the full-target formula
  // regardless of destination mode (D6, write-time canon).
  const degenerate = profile({ capture: { output_dir: 'vault/handbook' } });
  const flat = entry();
  const chapterFile = join(degenerate.publish.chapters_dir, chapterRelPath(flat));
  const legacy = legacyStaticEmbedPath(chapterFile, degenerate.capture.output_dir, flat.slug, '01.png');
  const fullTarget = embedPath(chapterFile, chapterAssetDir(degenerate, flat), '01.png');
  assert.equal(legacy, '/items/01.png');
  assert.equal(fullTarget, 'items/01.png');
  assert.notEqual(legacy, fullTarget, 'the two spellings must diverge in the degenerate case');
});

test('separator normalization: a backslash-authored output_dir still yields a POSIX result', () => {
  const winStyle = profile({ capture: { output_dir: 'vault\\handbook\\assets' } });
  assert.equal(chapterAssetDir(winStyle, entry()), 'vault/handbook/assets/items');
});

test('F4: an ABSOLUTE capture.output_dir stays absolute through chapterAssetDir/chapterFullPath (join preserves the root)', () => {
  const p = profile({
    capture: { output_dir: '/vault/handbook/assets' },
    publish: { chapters_dir: '/vault/handbook' },
  });
  assert.equal(chapterAssetDir(p, entry()), '/vault/handbook/assets/items');
  assert.equal(chapterAssetDir(p, entry({ group: 'admin', group_title: 'Admin' })), '/vault/handbook/assets/admin/items');
  // dirname of an absolute chapter file must also stay absolute.
  const chapterFile = '/vault/handbook/items.md';
  assert.equal(embedPath(chapterFile, chapterAssetDir(p, entry()), '01.png'), 'assets/items/01.png');
});

test('F4: an absolute-rooted migration fact/halt path is never silently downgraded to relative', () => {
  const p = profile({
    capture: { output_dir: '/vault/handbook/assets' },
    publish: { chapters_dir: '/vault/handbook', index_file: '/vault/SUMMARY.md' },
  });
  const old = entry({ group: 'admin', group_title: 'Admin' });
  const next = entry({ group: 'management', group_title: 'Admin' });
  const facts = manualMigrationChecklist(p, old, next);
  assert.equal(findFact(facts, 'current-chapter-path').path, '/vault/handbook/management/items.md');
  assert.equal(findFact(facts, 'old-asset-dir-gone').path, '/vault/handbook/assets/admin/items');
});

test('F4: "." and ".." segments normalize through join/dirname (parent-segment collapsing)', () => {
  const p = profile({ capture: { output_dir: 'vault/handbook/groups/../assets' } });
  // 'vault/handbook/groups/../assets' collapses to 'vault/handbook/assets' before the slug joins.
  assert.equal(chapterAssetDir(p, entry()), 'vault/handbook/assets/items');
  // A relative '..' climbing above its own start has nothing to collapse against and is kept.
  const p2 = profile({ capture: { output_dir: '../assets' } });
  assert.equal(chapterAssetDir(p2, entry()), '../assets/items');
  // An absolute '..' above the root collapses away entirely (POSIX '/..' === '/').
  const p3 = profile({ capture: { output_dir: '/../assets' } });
  assert.equal(chapterAssetDir(p3, entry()), '/assets/items');
});

test('R2-F4: mixed rootedness (one absolute, one relative path) THROWS rather than diffing garbage', () => {
  // An absolute asset dir diffed against a relative chapter file (or vice versa) would silently
  // discard one side's real root and produce a nonsense delta that still LOOKS like a valid
  // relative path — fail loud instead.
  assert.throws(
    () => embedPath('/vault/handbook/items.md', 'vault/handbook/assets/items', '01.png'),
    /mixed rootedness/,
  );
  assert.throws(
    () => embedPath('vault/handbook/items.md', '/vault/handbook/assets/items', '01.png'),
    /mixed rootedness/,
  );
});

test('R2-F4: both-absolute paths still produce the correct relative delta (the guard does not over-trigger)', () => {
  assert.equal(
    embedPath('/vault/handbook/items.md', '/vault/handbook/assets/items', '01.png'),
    'assets/items/01.png',
  );
});

test('R2-F4: both-relative paths are unaffected by the guard', () => {
  assert.equal(
    embedPath('vault/handbook/items.md', 'vault/handbook/assets/items', '01.png'),
    'assets/items/01.png',
  );
});

// =================================================================================================
// locateChapterLine
// =================================================================================================

test('locateChapterLine matches a markdown link and reports the nearest preceding heading', () => {
  // F1: only depth >= 2 anchors a containerTitle — a group container is always '##', never a
  // bare '#' (which could equally be a document title or, in a non-Markdown index, a YAML
  // comment). See the F1-specific tests below for the depth-1-is-not-a-container fixtures.
  const indexLines = ['# Handbook', '## Items Section', '', '- [Items](handbook/items.md)', ''];
  const result = locateChapterLine(indexLines, 'handbook/items.md');
  assert.equal(result.present, true);
  assert.equal(result.containerTitle, 'Items Section');
  assert.equal(result.multiple, false);
});

test('F1: a depth-1 heading never anchors a containerTitle (document title, never a group container)', () => {
  const indexLines = ['# Handbook', '- [Items](handbook/items.md)'];
  const result = locateChapterLine(indexLines, 'handbook/items.md');
  assert.equal(result.present, true);
  assert.equal(result.containerTitle, null, 'a lone depth-1 heading must not be reported as a container');
});

// Round-13 audit — DELIBERATELY UNTESTED, not a gap: collectContainerHeadings/locateChapterLine's
// container-anchoring check is `heading[1].length >= 2` (collectContainerHeadings's `m[1].length
// >= 2` guard and locateChapterLine's own `heading[1].length >= 2` ternary), so nothing in this
// file distinguishes it from a narrower `=== 2`. No fixture anywhere uses a depth-3 (###)
// heading. Left unpinned on purpose: collectContainerHeadings's own leading comment (D6
// convention) states a group container is ALWAYS `##`, so `>= 2`'s extra permissiveness beyond
// exactly-2 is not something the design currently depends on — pinning a `###` container would
// assert a behavior nobody has decided to support, not close a real gap. If a future round wants
// `###` containers to be first-class, that is a design decision, not a test-coverage fix — raise
// it separately rather than re-flagging this as an audit finding.

test('F1: findContainer classifies a mkdocs.yml-shaped YAML comment as non-heading (manual-wiring), never headings-form', () => {
  // A single '#'-prefixed comment line, exactly as a real mkdocs.yml nav: block would carry —
  // must not be mistaken for evidence of a Markdown headings-form index.
  const indexLines = ['# Main navigation', 'nav:', '  - Home: index.md', '  - Admin: admin/index.md'];
  assert.deepEqual(findContainer(indexLines, 'Admin'), { kind: 'non-heading' });
});

test('F1: findContainer classifies a GitBook "# Summary" + nested-list file as non-heading, never headings-form', () => {
  // A GitBook SUMMARY.md: one H1 document title, then nested bullet lists — no real '##' group
  // containers anywhere, so findContainer ITSELF is unchanged and still classifies this shape as
  // non-heading (never headings-form). [#223, 1.10.0] that verdict is no longer manual-wiring's
  // final word for a shape like this one: the adapter falls through to wireNestedListChapter,
  // which DOES auto-wire this exact bounded plain-label nested-list subset — see the
  // wireNestedListChapter suite below for the write-side behavior findContainer itself never
  // attempts (it only classifies; it never mutates the index).
  const indexLines = [
    '# Summary',
    '',
    '* [Introduction](README.md)',
    '* Admin',
    '  * [Items](admin/items.md)',
  ];
  assert.deepEqual(findContainer(indexLines, 'Admin'), { kind: 'non-heading' });
});

test('F1: new-container depth follows an EXISTING depth->=2 group container, never the H1 document title depth', () => {
  const indexLines = ['# Title', '## Admin', '- [[items]]'];
  const result = findContainer(indexLines, 'Billing');
  assert.deepEqual(result, { kind: 'zero', headingDepth: 2 });
});

test('F1: an H1 document title never produces a spurious wrong-container halt', () => {
  // Even though the H1 "Handbook" text differs from the group_title being checked, it must never
  // be reported as containerTitle at all (containerTitle stays null — no wrong-container mismatch
  // can be derived from a document title).
  const indexLines = ['# Handbook', '- [Items](handbook/admin/items.md)'];
  const result = locateChapterLine(indexLines, 'handbook/admin/items.md');
  assert.equal(result.containerTitle, null);
});

test('R3-F2(c): a depth-1 heading RESETS the current container to null', () => {
  const indexLines = ['## Admin', '# Appendix', '- [Items](admin/items.md)'];
  const result = locateChapterLine(indexLines, 'admin/items.md');
  assert.equal(result.present, true);
  assert.equal(result.containerTitle, null, 'the H1 ends the preceding ## Admin section');
});

test('R3-F1: a present line under a padded-title container converges via containerTitleMatches (no wrong-container halt)', () => {
  const indexLines = ['## Admin', '- [Items](handbook/admin/items.md)'];
  const result = locateChapterLine(indexLines, 'handbook/admin/items.md');
  assert.equal(result.present, true);
  assert.equal(result.containerTitle, 'Admin');
  // The manifest's own group_title is padded — a naive `result.containerTitle ===
  // entry.group_title` would fail ('Admin' !== '  Admin  ') and spuriously wrong-container-halt.
  const paddedEntry = entry({ group: 'admin', group_title: '  Admin  ' });
  assert.equal(containerTitleMatches(result.containerTitle, paddedEntry), true);
});

test('R3-F1: containerTitleMatches correctly reports a mismatch for a genuinely different container', () => {
  const paddedEntry = entry({ group: 'admin', group_title: '  Admin  ' });
  assert.equal(containerTitleMatches('Billing', paddedEntry), false);
  assert.equal(containerTitleMatches(null, paddedEntry), false, 'a null containerTitle never matches');
});

test('containerTitleMatches: entry pin [round-13 audit] — a genuinely different real title matches ITSELF, not a hardcoded "Admin"', () => {
  // Round-13 audit finding: every containerTitleMatches call in the file reuses the SAME
  // paddedEntry (group_title '  Admin  ', trimming to 'Admin'), so a mutant replacing
  // `trimmedTitle(entry)` with the hardcoded literal 'Admin' would pass every existing
  // assertion. An entry with a genuinely DIFFERENT title proves the entry's own group_title is
  // read, not a constant — checked both ways (matches its own title, does not match 'Admin').
  const opsEntry = entry({ slug: 'x', group: 'ops', group_title: '  Ops  ' });
  assert.equal(containerTitleMatches('Ops', opsEntry), true, "must match the entry's own (trimmed) title");
  assert.equal(containerTitleMatches('Admin', opsEntry), false, "must not match 'Admin' for an Ops entry");
});

test('R3-F2(a): a commented-out YAML nav row must not report present:true (false completion)', () => {
  const indexLines = ['nav:', '  # - Items: handbook/admin/items.md'];
  const result = locateChapterLine(indexLines, 'handbook/admin/items.md');
  assert.equal(result.present, false, 'a commented-out row is not a real TOC entry');
});

test('R4-F2: a TOC row inside an HTML comment must not report present:true (false completion)', () => {
  const indexLines = ['nav:', '<!-- - [[items]] -->'];
  assert.equal(locateChapterLine(indexLines, 'items').present, false);
});

test('R4-F2: a TOC row inside a fenced code block must not report present:true (false completion)', () => {
  const indexLines = ['nav:', '```', '- [[items]]', '```'];
  assert.equal(locateChapterLine(indexLines, 'items').present, false);
});

test('R4-F2 control: an ACTIVE (non-inert) row is still found', () => {
  const indexLines = ['nav:', '- [[items]]'];
  assert.equal(locateChapterLine(indexLines, 'items').present, true);
});

// Round-5 F1: `containerTitle: null` alone is ambiguous between "non-heading file" and "active
// line outside any container in a HEADINGS-form file" — the new `indexForm` field disambiguates.

test('R5-F1: an active row ABOVE its ## container in a headings-form file is UNCONTAINED (indexForm:headings, containerTitle:null)', () => {
  const indexLines = ['- [[items]]', '## Admin'];
  const result = locateChapterLine(indexLines, 'items');
  assert.equal(result.present, true);
  assert.equal(result.indexForm, 'headings');
  assert.equal(result.containerTitle, null, 'uncontained, not "same as non-heading"');
});

test('R5-F1: a genuine non-heading file reports indexForm:non-heading', () => {
  const indexLines = ['nav:', '- [[items]]'];
  const result = locateChapterLine(indexLines, 'items');
  assert.equal(result.present, true);
  assert.equal(result.indexForm, 'non-heading');
  assert.equal(result.containerTitle, null);
});

test('R5-F1: an H1 RESET still reports indexForm:headings (the file itself has depth>=2 headings, only this line is uncontained)', () => {
  const indexLines = ['## Admin', '# Appendix', '- [[items]]'];
  const result = locateChapterLine(indexLines, 'items');
  assert.equal(result.present, true);
  assert.equal(result.indexForm, 'headings');
  assert.equal(result.containerTitle, null);
});

test('R3-F2(b): a "##"-spelled YAML comment no longer defeats the depth>=2 heuristic (structural check)', () => {
  const indexLines = ['## Secondary navigation', 'nav:', '  - Admin: admin/items.md'];
  assert.deepEqual(findContainer(indexLines, 'Admin'), { kind: 'non-heading' });
});

test('R3-F2(b): the YAML-mapping structural check does not misclassify a real Obsidian INDEX.md frontmatter block', () => {
  const indexLines = ['---', 'type: handbook', 'status: active', '---', '', '## Admin', '- [[items]]'];
  const result = findContainer(indexLines, 'Admin');
  assert.equal(result.kind, 'single');
  assert.equal(result.location.title, 'Admin');
});

test('R4-F3: an UNCLOSED leading "---" is a plain YAML document-start marker, not frontmatter — the structural check still runs', () => {
  // Round-3's frontmatter-skip logic advanced past EOF when no closing '---' existed, so
  // `.slice(i)` silently returned [] and the YAML-mapping check never ran on the rest of the
  // document — misclassifying this exact shape as headings-form again.
  const indexLines = ['---', '## Secondary navigation', 'nav:', '  - Orders: x.md'];
  assert.deepEqual(findContainer(indexLines, 'Orders'), { kind: 'non-heading' });
});

test('R4-F3 regression guard: a PROPERLY closed frontmatter block + real headings still classifies as headings-form', () => {
  const indexLines = ['---', 'type: handbook', 'status: active', '---', '', '## Billing', '- [[items]]'];
  const result = findContainer(indexLines, 'Billing');
  assert.equal(result.kind, 'single');
  assert.equal(result.location.title, 'Billing');
});

test('locateChapterLine matches a wikilink target (alias stripped)', () => {
  const indexLines = ['## Admin', '- [[items|Items]]'];
  const result = locateChapterLine(indexLines, 'items');
  assert.equal(result.present, true);
  assert.equal(result.containerTitle, 'Admin');
});

test('locateChapterLine matches a bare (unlabeled sequence scalar) YAML nav: path entry', () => {
  const indexLines = ['nav:', '  - handbook/items.md'];
  const result = locateChapterLine(indexLines, 'handbook/items.md');
  assert.equal(result.present, true);
  assert.equal(result.containerTitle, null, 'non-heading forms report a null containerTitle');
});

test('F5: locateChapterLine matches a canonical LABELED MkDocs nav row (YAML mapping, not a bare scalar)', () => {
  // The realistic MkDocs nav: shape — most real-world configs use `- Label: path`, not a bare
  // path sequence. The pre-fix bare-scalar-only fallback treated the whole "Items: handbook/
  // admin/items.md" string as the target, which never equals the plain path and would have
  // manual-wiring-halted forever on a normal labeled nav entry.
  const indexLines = ['nav:', '  - Items: handbook/admin/items.md'];
  const result = locateChapterLine(indexLines, 'handbook/admin/items.md');
  assert.equal(result.present, true);
});

test('F5: locateChapterLine matches a Markdown link with an angle-bracket-wrapped destination', () => {
  const indexLines = ['- [Items](<handbook/admin/items.md>)'];
  const result = locateChapterLine(indexLines, 'handbook/admin/items.md');
  assert.equal(result.present, true);
});

test('F5: locateChapterLine matches a Markdown link carrying an optional title', () => {
  const indexLines = ['- [Items](handbook/admin/items.md "Admin items")'];
  const result = locateChapterLine(indexLines, 'handbook/admin/items.md');
  assert.equal(result.present, true);
});

test('F5: locateChapterLine matches an angle-bracket destination WITH a title', () => {
  const indexLines = [`- [Items](<handbook/admin/items.md> 'Admin items')`];
  const result = locateChapterLine(indexLines, 'handbook/admin/items.md');
  assert.equal(result.present, true);
});

// Round-2 F3: the labeled-row value is decoded as a YAML scalar (quotes stripped, a trailing
// end-of-line comment stripped) — without this, a quoted or commented labeled row would
// present:false forever and the documented re-run after a manual-wiring halt never converges.

test('R2-F3: a labeled MkDocs row whose value is QUOTED converges', () => {
  const indexLines = ['nav:', '  - Items: "handbook/admin/items.md"'];
  assert.equal(locateChapterLine(indexLines, 'handbook/admin/items.md').present, true);
});

test('R2-F3: a labeled MkDocs row carrying a trailing end-of-line comment converges', () => {
  const indexLines = ['nav:', '  - Items: handbook/admin/items.md # grouped'];
  assert.equal(locateChapterLine(indexLines, 'handbook/admin/items.md').present, true);
});

test('R2-F3: a labeled MkDocs row that is BOTH quoted and commented converges', () => {
  const indexLines = ['nav:', '  - Items: "handbook/admin/items.md" # grouped'];
  assert.equal(locateChapterLine(indexLines, 'handbook/admin/items.md').present, true);
});

// Round-5 F3: a naive [^)]+ Markdown link capture stops at the FIRST ')' — profile paths are
// unrestricted, so a legal dir like 'docs(v2)' breaks the capture before the link's real close.

test('R5-F3: an angle-wrapped destination containing literal parens converges (the exact probe)', () => {
  const indexLines = ['[Orders](<docs(v2)/admin/orders.md>)'];
  assert.equal(locateChapterLine(indexLines, 'docs(v2)/admin/orders.md').present, true);
});

test('R5-F3: an unwrapped destination with balanced parens converges', () => {
  const indexLines = ['[Orders](a(b)c.md)'];
  assert.equal(locateChapterLine(indexLines, 'a(b)c.md').present, true);
});

test('R6-F2: an escaped-paren destination decodes to the filesystem-derived spelling (the exact codex probe)', () => {
  const indexLines = ['[Orders](docs\\(v2\\)/admin/orders.md)'];
  assert.equal(locateChapterLine(indexLines, 'docs(v2)/admin/orders.md').present, true);
});

test('R6-F3: an escaped bracket inside the link LABEL does not hide the destination (the exact codex title probe)', () => {
  const indexLines = ['- [Plans \\[Beta\\]](handbook/admin/plans.md)'];
  assert.equal(locateChapterLine(indexLines, 'handbook/admin/plans.md').present, true);
});

test('R5-F3/R6-F2: an unwrapped destination with an escaped paren converges against the DECODED (filesystem-derived) spelling', () => {
  // The real caller's expectedTarget is always computed from filesystem-derived path segments,
  // which never contain backslash-escapes — 'admin(archived).md' is the actual file, and the
  // SOURCE spells it escaped only because CommonMark syntax requires it. Passing the escaped
  // spelling as expectedTarget (as this fixture originally did) masked the R6-F2 decoding bug —
  // it happened to "pass" by comparing two equally-wrong (still-escaped) strings.
  const indexLines = ['[Orders](admin\\(archived\\).md)'];
  assert.equal(locateChapterLine(indexLines, 'admin(archived).md').present, true);
});

test('locateChapterLine: the same target on two lines => multiple (duplicate-line halt path)', () => {
  const indexLines = ['- [Items](handbook/items.md)', '- [Items](handbook/items.md)'];
  const result = locateChapterLine(indexLines, 'handbook/items.md');
  assert.equal(result.multiple, true);
});

test('locateChapterLine: THREE duplicate index lines still report multiple:true [round-13 audit]', () => {
  // Round-13 audit finding: both existing duplicate-line fixtures (here and R14-F3 below) use
  // exactly 2 occurrences, so `matches.length > 1` was indistinguishable from `=== 2`. A third
  // identical line proves the ambiguous-duplicate-line halt path still fires.
  //
  // Also asserts `present` (round-13 review finding 4, this fixture's own gap): `present:
  // matches.length > 0` is a SEPARATE boundary from `multiple: matches.length > 1` on the same
  // `matches` array — narrowing `present` to `=== 1` survives if only `multiple`/`matches.length`
  // are checked, returning the self-contradictory `{present: false, multiple: true}` a
  // present-first caller could misread as "insert, don't halt."
  const indexLines = [
    '- [Items](handbook/items.md)',
    '- [Items](handbook/items.md)',
    '- [Items](handbook/items.md)',
  ];
  const result = locateChapterLine(indexLines, 'handbook/items.md');
  assert.equal(result.present, true);
  assert.equal(result.multiple, true);
  assert.equal(result.matches.length, 3);
});

test('locateChapterLine does not match a different chapter or a same-basename chapter in another group', () => {
  const indexLines = ['- [Items](handbook/admin/items.md)'];
  assert.equal(locateChapterLine(indexLines, 'handbook/items.md').present, false);
  assert.equal(locateChapterLine(indexLines, 'handbook/billing/items.md').present, false);
});

test('locateChapterLine on an empty index => present: false', () => {
  assert.equal(locateChapterLine([], 'handbook/items.md').present, false);
});

test('locateChapterLine coordinate-system fixture: vault-root SUMMARY.md — naked <group>/<slug>.md must NOT match', () => {
  // index_file: vault/SUMMARY.md, chapter: vault/handbook/admin/items.md => expectedTarget =
  // handbook/admin/items.md.
  const indexLines = ['- [Items](admin/items.md)'];
  assert.equal(locateChapterLine(indexLines, 'handbook/admin/items.md').present, false);
});

test('locateChapterLine coordinate-system fixture: repo-root SUMMARY.md — naked <group>/<slug>.md must NOT match', () => {
  // index_file: SUMMARY.md (repo root), chapter: vault/handbook/admin/items.md => expectedTarget =
  // vault/handbook/admin/items.md.
  const indexLines = ['- [Items](admin/items.md)'];
  assert.equal(locateChapterLine(indexLines, 'vault/handbook/admin/items.md').present, false);
  // The correctly-qualified line DOES match.
  const qualified = ['- [Items](vault/handbook/admin/items.md)'];
  assert.equal(locateChapterLine(qualified, 'vault/handbook/admin/items.md').present, true);
});

test('normalized comparisons: ./ prefix and backslash separators are insensitive', () => {
  assert.equal(locateChapterLine(['- [Items](./vault/x)'], 'vault/x').present, true);
  assert.equal(locateChapterLine(['- [Items](vault\\x)'], 'vault/x').present, true);
});

// =================================================================================================
// D6 — locateChapterLine {wikilink} .md-fold (D-6, opt-in)
// =================================================================================================

test('D-6: {wikilink:true} folds a terminal .md off a line target so it matches the extensionless wanted target', () => {
  const indexLines = ['- [[handbook/items.md]]'];
  assert.equal(
    locateChapterLine(indexLines, 'handbook/items', { wikilink: true }).present,
    true,
    'opt-in fold recognises the .md-suffixed row as the same target',
  );
});

test('D-6: the .md-fold is OPT-IN — default (no options) leaves a .md-suffixed line target unmatched', () => {
  const indexLines = ['- [[handbook/items.md]]'];
  assert.equal(
    locateChapterLine(indexLines, 'handbook/items').present,
    false,
    'path-mode/pre-1.8.0 callers must stay byte-identical: no fold unless explicitly requested',
  );
});

test('#311: path mode (default options) treats an extensionless hand-authored line as UNMATCHED — by design (canonical row appended, divergent row retained)', () => {
  // Reverse of the opt-in fold: here the LINE dropped the `.md` (`handbook/items`) while the
  // wanted target carries it (`handbook/items.md`). In path mode the `.md` is load-bearing —
  // `items` and `items.md` are DIFFERENT hrefs — so this divergent line must NOT be folded to a
  // match (that would be a false-positive against a genuinely-different resource). Left unmatched,
  // step 0's flat-entry-absent branch appends the canonical `.md` row and RETAINS this divergent
  // row alongside it (append-and-retain) — the link-integrity gate does not reject the retained row.
  assert.equal(
    locateChapterLine(['- [Items](handbook/items)'], 'handbook/items.md').present,
    false,
    'path-mode byte-identity is intentional (#311): an extensionless divergent line stays unmatched',
  );
});

// =================================================================================================
// D7 — classifyChapterWiring
// =================================================================================================

function scan(...matches) {
  return { matches };
}

test('classifyChapterWiring: no hits at all => absent', () => {
  assert.equal(classifyChapterWiring('handbook/items', 'items', scan(), scan()), 'absent');
});

test('classifyChapterWiring: exactly one qualified hit, no legacy hit => canonical', () => {
  const qScan = scan({ line: '- [[handbook/items]]', containerTitle: null });
  assert.equal(classifyChapterWiring('handbook/items', 'items', qScan, scan()), 'canonical');
});

test('classifyChapterWiring: exactly one legacy (bare) hit, no qualified hit => legacy', () => {
  const lScan = scan({ line: '- [[items]]', containerTitle: null });
  assert.equal(classifyChapterWiring('handbook/items', 'items', scan(), lScan), 'legacy');
});

test('classifyChapterWiring: one qualified + one DISTINCT legacy hit => duplicate (malformed double-reference row)', () => {
  const qScan = scan({ line: '- [[handbook/items]]', containerTitle: null });
  const lScan = scan({ line: '- [[items]]', containerTitle: null });
  assert.equal(classifyChapterWiring('handbook/items', 'items', qScan, lScan), 'duplicate');
});

test('classifyChapterWiring: two qualified hits (no legacy) => duplicate', () => {
  const qScan = scan(
    { line: '- [[handbook/items]]', containerTitle: null },
    { line: '- [[handbook/items]]', containerTitle: 'Admin' },
  );
  assert.equal(classifyChapterWiring('handbook/items', 'items', qScan, scan()), 'duplicate');
});

test('D-7 root-topology dedup (codex R3 BLOCKER regression pin): qualified === legacyBare must NOT double-count into duplicate', () => {
  // vaultRelChaptersDir === '' with a flat entry makes qualified === legacyBare === slug (§0a
  // "SAFE, no halt" root topology) — qScan and lScan searched the IDENTICAL string, so they found
  // the SAME single index line twice, not two independent hits.
  const qScan = scan({ line: '- [[items]]', containerTitle: null });
  const lScan = scan({ line: '- [[items]]', containerTitle: null });
  assert.equal(classifyChapterWiring('items', 'items', qScan, lScan), 'canonical', 'must dedup, not duplicate');
});

// =================================================================================================
// findContainer
// =================================================================================================

test('findContainer: zero matching headings => create, at the sibling heading depth', () => {
  const result = findContainer(['## Admin', '- x'], 'Billing');
  assert.deepEqual(result, { kind: 'zero', headingDepth: 2 });
});

test('findContainer: single matching heading => append location', () => {
  const result = findContainer(['## Admin', '- x'], 'Admin');
  assert.equal(result.kind, 'single');
  assert.equal(result.location.title, 'Admin');
  assert.equal(result.location.depth, 2);
});

test('findContainer: multiple matching headings => container-ambiguous', () => {
  const result = findContainer(['## Admin', '- x', '## Admin', '- y'], 'Admin');
  assert.equal(result.kind, 'multiple');
  assert.equal(result.matches.length, 2);
});

test('findContainer: THREE matching headings still classify as multiple, not "zero" [round-13 audit]', () => {
  // Round-13 audit finding: the ONLY multiple-heading fixture in the file uses exactly 2 matches,
  // so `matches.length > 1` was indistinguishable from `matches.length === 2`. Under that
  // narrowing, a THIRD matching heading falls through BOTH the `multiple` and `single` checks to
  // the `zero` branch — the worst outcome of any boundary in this audit: not a missed flag, but a
  // wrong classification telling the caller to CREATE a new section when three real ambiguous
  // candidates already exist.
  const result = findContainer(['## Admin', '- x', '## Admin', '- y', '## Admin', '- z'], 'Admin');
  assert.equal(result.kind, 'multiple');
  assert.equal(result.matches.length, 3);
});

// Round-5 F2: findContainer must run on the SAME sanitized view locateChapterLine uses — a
// commented-out heading is not a real container, and location.index must still refer to the
// ORIGINAL indexLines array (sanitization is 1:1/newline-preserving).

test('R5-F2: a commented-out heading is not a container — kind zero when it was the only "Admin" match', () => {
  const indexLines = ['<!-- ## Admin -->', '## Billing'];
  assert.deepEqual(findContainer(indexLines, 'Admin'), { kind: 'zero', headingDepth: 2 });
});

test('R5-F2: a heading-shaped line INSIDE a multi-line HTML comment (genuinely at column 0) is still not a container', () => {
  // Same-line comments never put '##' at column 0 anyway (HEADING_RE requires the line to START
  // with '#'), so they cannot prove findContainer actually runs on the SANITIZED view — this
  // multi-line comment DOES: '## Admin' sits on its own line, genuinely at column 0, and would be
  // matched as a real heading by RAW scanning; only sanitization correctly blanks it first.
  const indexLines = ['<!--', '## Admin', '-->', '## Billing'];
  assert.deepEqual(findContainer(indexLines, 'Admin'), { kind: 'zero', headingDepth: 2 });
});

test('R5-F2: an active heading plus a commented-out same-title heading => single, not multiple', () => {
  const indexLines = ['## Admin', '<!-- ## Admin -->'];
  const result = findContainer(indexLines, 'Admin');
  assert.equal(result.kind, 'single');
  assert.equal(result.location.index, 0);
  // location.index refers to the ORIGINAL array — confirm it actually resolves there.
  assert.equal(indexLines[result.location.index], '## Admin');
});

test('findContainer: a non-heading index (YAML nav) => manual-wiring classification', () => {
  assert.deepEqual(findContainer(['nav:', '  - a', '  - b'], 'Admin'), { kind: 'non-heading' });
});

test('R6-F1 manual-wiring convergence pair: absent then present after the user adds the container+line', () => {
  const before = ['nav:', '  - other/x.md'];
  assert.equal(locateChapterLine(before, 'admin/items.md').present, false);
  assert.equal(findContainer(before, 'Admin').kind, 'non-heading');

  const after = ['nav:', '  - other/x.md', '  - admin/items.md'];
  assert.equal(locateChapterLine(after, 'admin/items.md').present, true, 'step-0 short-circuit on re-run');
});

test('R7-F1 wrong-container fixture: the line exists but under the WRONG container', () => {
  const indexLines = ['## Billing', '- [Items](handbook/admin/items.md)', '## Admin'];
  const result = locateChapterLine(indexLines, 'handbook/admin/items.md');
  assert.equal(result.present, true);
  assert.equal(result.containerTitle, 'Billing');
  assert.notEqual(result.containerTitle, 'Admin', 'must not be treated as complete under the wrong container');
});

// =================================================================================================
// [1.11.0] #330 prep — indexView, the exported "name the expression" extraction of
// locateChapterLine's own sanitized view. Direct characterization tests: nothing exercised this
// expression on its own before extraction, since it lived inline. The extraction ITSELF was
// behavior-preserving (plan Locked scope decision 5) — that is a different claim from "no change to
// locateChapterLine's return shape", which stopped being true once that shape later gained `index`
// on each match record (#330 round-2 review, additive — the .d.mts now publishes it). Collapsing
// those two claims into one is exactly what made the original wording here go stale; see the
// library's own corrected docstring above `indexView` for the same fix.
// =================================================================================================

test('indexView [characterization]: a plain file with no inert content round-trips element-for-element', () => {
  const indexLines = ['- Admin', '  - [Items](admin/items.md)'];
  assert.deepEqual(indexView(indexLines), indexLines);
});

test('indexView [characterization]: an HTML comment spanning its own lines is blanked, matching stripInertContexts', () => {
  const indexLines = ['<!--', '## Admin', '-->', '## Billing'];
  const view = indexView(indexLines);
  assert.equal(view.length, indexLines.length, 'newline-preserving 1:1 — same line count');
  assert.ok(!/## Admin/.test(view.join('\n')), 'the commented-out heading must not survive the view');
  assert.ok(/## Billing/.test(view.join('\n')), 'the real heading must survive the view');
});

test('indexView [parity]: locateChapterLine sees exactly what indexView produces — a match inside a multiline comment never reports present', () => {
  // Ties the extraction back to its one caller (round-26: sharing the expression is true by
  // construction, but the shared VIEW must actually be what the caller scans, not a duplicate).
  const indexLines = ['<!--', '- [Items](admin/items.md)', '-->', '- Billing'];
  const view = indexView(indexLines);
  assert.ok(!/admin\/items\.md/.test(view.join('\n')), 'indexView blanks the commented-out target');
  assert.equal(
    locateChapterLine(indexLines, 'admin/items.md').present,
    false,
    'locateChapterLine must report the same absence indexView implies — one recognizer, not two',
  );
});

// =================================================================================================
// #223 [1.10.0] — wireNestedListChapter (nested-list / GitBook SUMMARY.md write automation)
// =================================================================================================
// Reached only when findContainer(...) returned {kind:'non-heading'} AND step 0 found no existing
// chapter line (plan §4/§5). Fixtures below drive the real ABSENT-line write outcomes (SINGLE / ZERO
// / MULTIPLE) or prove a specific §5.1 guard refuses ('not-a-list') — grouped to mirror the plan's
// own guard inventory (§8/§9.1) so a fixture maps back to the guard it isolates. Distinct group
// titles/markers/indents are used throughout (never all 'Admin'/2-space) so a constant-hardcoding
// mutant cannot hide behind a repeated fixture shape (round-13 discipline).

// -------------------------------------------------------------------------------------------------
// SINGLE / ZERO / MULTIPLE — the three real write outcomes
// -------------------------------------------------------------------------------------------------

test('wireNestedListChapter SINGLE w/children: child inserted after the LAST C-indent child, container marker reused (3-space C kills a hardcode-indent-2 mutant)', () => {
  const indexLines = [
    '# Summary',
    '',
    '* Introduction',
    '* Admin',
    '   * [Orders](admin/orders.md)',
    '   * [Billing](admin/billing.md)',
    '* Other',
    '',
  ];
  const result = wireNestedListChapter(indexLines, 'Admin', '[Items](admin/items.md)');
  assert.equal(result.kind, 'inserted');
  assert.equal(result.created, false, 'the existing "Admin" container is reused, not re-created');
  assert.deepEqual(result.newLines, [
    '# Summary',
    '',
    '* Introduction',
    '* Admin',
    '   * [Orders](admin/orders.md)',
    '   * [Billing](admin/billing.md)',
    '   * [Items](admin/items.md)',
    '* Other',
    '',
  ]);
});

test('wireNestedListChapter SINGLE w/children, DIVERGENT container/child markers: the inserted child reuses the EXISTING CHILD marker, not the container marker (R1 regression catcher)', () => {
  // The container is "+"-marked but its existing child is "-"-marked — the validator's own forward
  // pass tracks only child INDENT (chapter-paths.mjs's childIndentSeen), never child MARKER, so this
  // shape is accepted. If the insertion reused containerMarker ("+") here, the new line would carry a
  // DIFFERENT marker than its sibling "- [Orders]…" -> CommonMark starts a fresh list block at the
  // marker change, silently splitting the sublist instead of appending to it.
  const indexLines = ['+ Admin', '  - [Orders](admin/orders.md)'];
  const result = wireNestedListChapter(indexLines, 'Admin', '[Items](admin/items.md)');
  assert.equal(result.kind, 'inserted');
  assert.equal(result.created, false);
  assert.deepEqual(result.newLines, ['+ Admin', '  - [Orders](admin/orders.md)', '  - [Items](admin/items.md)']);
});

test('wireNestedListChapter SINGLE, first-ever child: with NO existing child bullet, the inserted child falls back to the CONTAINER marker ("?? containerMarker" branch)', () => {
  const indexLines = ['* Admin'];
  const result = wireNestedListChapter(indexLines, 'Admin', '[Items](admin/items.md)');
  assert.equal(result.kind, 'inserted');
  assert.equal(result.created, false);
  assert.deepEqual(result.newLines, ['* Admin', '  * [Items](admin/items.md)']);
});

test('wireNestedListChapter SINGLE no-children: child inserted immediately under the container at the default C=2 (no child bullet anywhere in the file)', () => {
  const indexLines = ['# Summary', '', '- Introduction', '- Admin', '- Other'];
  const result = wireNestedListChapter(indexLines, 'Admin', '[Items](admin/items.md)');
  assert.equal(result.kind, 'inserted');
  assert.equal(result.created, false);
  assert.deepEqual(result.newLines, [
    '# Summary',
    '',
    '- Introduction',
    '- Admin',
    '  - [Items](admin/items.md)',
    '- Other',
  ]);
});

test('wireNestedListChapter ZERO (create): bare-label container + child spliced after the LAST bullet line, file ends with the list (no trailing prose)', () => {
  const indexLines = ['# Summary', '', '* Introduction', '* Billing'];
  const result = wireNestedListChapter(indexLines, 'Admin', '[Items](admin/items.md)');
  assert.equal(result.kind, 'inserted');
  assert.equal(result.created, true);
  assert.deepEqual(result.newLines, [
    '# Summary',
    '',
    '* Introduction',
    '* Billing',
    '* Admin',
    '  * [Items](admin/items.md)',
  ]);
});

test('wireNestedListChapter MULTIPLE: THREE indent-0 bullets sharing the same label all count (kills a matches.length===2 mutant)', () => {
  const indexLines = ['- Ops', '- Billing', '- Ops', '- Support', '- Ops'];
  const result = wireNestedListChapter(indexLines, 'Ops', '[Runbook](ops/runbook.md)');
  assert.equal(result.kind, 'multiple');
  assert.deepEqual(result.matches, [
    { index: 0, label: 'Ops' },
    { index: 2, label: 'Ops' },
    { index: 4, label: 'Ops' },
  ]);
});

// -------------------------------------------------------------------------------------------------
// Rule-isolating not-a-list fixtures — each passes every OTHER guard, fails ONLY the one under test
// (removing that guard alone flips the fixture to wrongly 'inserted'). Verified by hand against the
// real chapter-paths.mjs: apply the named guard-removal mutation, confirm RED, restore, confirm
// git diff --stat is pristine before the next mutation — see the red-before-green log in the report.
// -------------------------------------------------------------------------------------------------

test('not-a-list, inert-identity guard [isolating]: a code-span container label ("`Admin`") is refused rather than silently treated as raw', () => {
  // WITH the guard: refused outright (BODY must equal its raw form). WITHOUT it: BODY would equal
  // SAN's blanked view for classification purposes while insertion still used the raw line, so the
  // label "`Admin`" (with backticks) would never equal "Admin" -> ZERO would CREATE a duplicate
  // "- Admin" beside the code-span one. That flip (not-a-list -> inserted) is what makes this
  // fixture genuinely isolating, unlike the multiline-comment mask-pair below.
  const indexLines = ['- `Admin`'];
  const result = wireNestedListChapter(indexLines, 'Admin', '[Items](admin/items.md)');
  assert.equal(result.kind, 'not-a-list');
});

test('not-a-list, plain-label allowlist / group_title side, backtick [R2 regression]: a code-span-wrapped manifest group_title ("`Admin`") is refused, never emitted as a duplicate container', () => {
  // The inert-identity guard above only refuses a backtick already present in the INDEX FILE body —
  // it has no reach over a manifest-supplied group_title, which never passes through stripInertContexts.
  // isPlainLabel is the ONLY guard standing between a backtick-bearing group_title and a false ZERO
  // create: WITHOUT the backtick in its denylist, '`Admin`'.trim() would pass isPlainLabel, never equal
  // the existing plain "Admin" label, and ZERO would CREATE a second "- `Admin`" container that renders
  // as a code-styled duplicate of "- Admin".
  const indexLines = ['- Admin'];
  const result = wireNestedListChapter(indexLines, '`Admin`', '[Items](admin/items.md)');
  assert.equal(result.kind, 'not-a-list');
});

test('not-a-list, plain-label allowlist / existing-label side [isolating]: an emphasis-wrapped container label is refused, not silently unwrapped', () => {
  // WITH the allowlist: refused. WITHOUT it: extractLabel legitimately returns "**Admin**" verbatim
  // (emphasis is not link/wikilink syntax it unwraps), which never equals "Admin" -> ZERO CREATES a
  // duplicate "- Admin" that renders as a second, DIFFERENTLY-STYLED "Admin" container.
  const indexLines = ['- [**Admin**](admin/index.md)'];
  const result = wireNestedListChapter(indexLines, 'Admin', '[Items](admin/items.md)');
  assert.equal(result.kind, 'not-a-list');
});

test('not-a-list, plain-label allowlist / existing-label side, SAME guard, further char-class variants (raw escape, entity, reference link, strikethrough, image)', () => {
  const variants = ['- Admin\\!', '- A &amp; B', '- [x][ref]', '- ~~Admin~~', '- ![x](y)'];
  for (const line of variants) {
    const result = wireNestedListChapter([line], 'Admin', '[Items](admin/items.md)');
    assert.equal(result.kind, 'not-a-list', `expected not-a-list for ${JSON.stringify(line)}`);
  }
});

test('not-a-list, plain-label allowlist / group_title side [isolating]: a construct-bearing group_title is refused, never emitted', () => {
  // WITH the allowlist: refused (checked BEFORE the forward pass even starts, independent of file
  // content). WITHOUT it: ZERO would emit "- **Admin**" as a new container, a rendered duplicate of
  // the plain "- Admin" already present.
  const indexLines = ['- Admin', '- Billing'];
  const result = wireNestedListChapter(indexLines, '**Admin**', '[Items](admin/items.md)');
  assert.equal(result.kind, 'not-a-list');
});

test('not-a-list, plain-label allowlist / leading ATX block-trigger [isolating]: "- # Admin" is refused (renders <h1>Admin</h1>, not a plain label)', () => {
  const indexLines = ['- # Admin'];
  const result = wireNestedListChapter(indexLines, 'Admin', '[Items](admin/items.md)');
  assert.equal(result.kind, 'not-a-list');
});

test('not-a-list, plain-label allowlist / leading nested list-marker [isolating]: "- 1. Intro" is refused', () => {
  const indexLines = ['- 1. Intro'];
  const result = wireNestedListChapter(indexLines, 'Intro', '[Items](admin/items.md)');
  assert.equal(result.kind, 'not-a-list');
});

test('not-a-list, plain-label allowlist / whitespace-collapse [isolating]: "A  B" (double interior space) is refused even against a groupTitle already collapsed to "A B"', () => {
  // WITHOUT the [ \t]{2,} check, "A  B" and "A B" render-collide in HTML though their source
  // differs, which would let a raw double-space label falsely match/duplicate a single-space title.
  const indexLines = ['- A  B'];
  const result = wireNestedListChapter(indexLines, 'A B', '[Items](admin/items.md)');
  assert.equal(result.kind, 'not-a-list');
});

test('not-a-list, thematic break at a CHILD indent [isolating]: a 4-space "- - -" (<hr> inside the item) is excluded, not accepted as a child', () => {
  // WITH the trimmed step-2 guard (any indent): refused. WITHOUT it (reverting to the old {0,3}-
  // leading-space form): the 4-space line matches the bullet regex, passes the 2..4 C-cap, and is
  // wrongly accepted as a real child of "Admin" -> inserted. A CHILD bullet's content never goes
  // through isPlainLabel (only indent-0 candidates do), so no other guard backstops this one.
  const indexLines = ['- Admin', '    - - -'];
  const result = wireNestedListChapter(indexLines, 'Admin', '[Items](admin/items.md)');
  assert.equal(result.kind, 'not-a-list');
});

test('not-a-list, thematic break at the root: "- - -" alone is excluded (double-guarded — also fails the label leading-marker rule, so this is a defensive rejection test, not a single-guard isolator)', () => {
  const indexLines = ['- - -'];
  const result = wireNestedListChapter(indexLines, 'Admin', '[Items](admin/items.md)');
  assert.equal(result.kind, 'not-a-list');
});

test('not-a-list, true single-space marker / 3-space padding [isolating]: "-   Admin" is refused as FOREIGN, not accepted with a trimmed label', () => {
  // WITHOUT the (?![ \t]) lookahead (old form with no space enforcement): the line matches with
  // content "  Admin", and parseNestedLabel trims it right back down to "Admin" — silently masking
  // the malformed marker spacing and misplacing the content column.
  const indexLines = ['-   Admin'];
  const result = wireNestedListChapter(indexLines, 'Admin', '[Items](admin/items.md)');
  assert.equal(result.kind, 'not-a-list');
});

test('not-a-list, true single-space marker / space+tab [isolating, R6-2]: "- \\tAdmin" is refused — closes the gap an earlier (?! ) (space-only) lookahead left open for a following TAB', () => {
  const indexLines = ['- \tAdmin'];
  const result = wireNestedListChapter(indexLines, 'Admin', '[Items](admin/items.md)');
  assert.equal(result.kind, 'not-a-list');
});

test('not-a-list, C-cap [isolating]: a 6-space "child" is a CommonMark indented-code line, not a real child', () => {
  // WITHOUT the 2..4 cap: childIndentSeen=6 is accepted, and the new child is spliced in at the SAME
  // 6-space indent — which CommonMark would render as an indented code block, not a list item.
  const indexLines = ['- Admin', '', '      - child'];
  const result = wireNestedListChapter(indexLines, 'Admin', '[Items](admin/items.md)');
  assert.equal(result.kind, 'not-a-list');
});

test('not-a-list, heading-reset / orphan-after-reset [isolating, R3-7]: a child after a depth-1 heading reset is an orphan, not still under the earlier container', () => {
  // WITHOUT the currentContainer=null reset on an ATX heading (or the orphan-child check that
  // consults it): the child would be silently accepted and resolved under "Admin" anyway — this is
  // the fixture that genuinely isolates the orphan-child guard (see the report note: a bare
  // orphan-child-before-any-top-bullet fixture is masked by the separate !sawTop guard, since there
  // is no other way in this grammar to reach "sawTop=true, currentContainer=null" except via a
  // heading reset).
  const indexLines = ['# Summary', '- Admin', '# Other', '  - child'];
  const result = wireNestedListChapter(indexLines, 'Admin', '[Items](admin/items.md)');
  assert.equal(result.kind, 'not-a-list');
});

test('not-a-list, orphan child before any top bullet (masked by !sawTop) [rejection, not independently isolating]: a document that opens directly with a child bullet has no container to attach to', () => {
  // In this grammar sawTop only ever becomes true AT the same moment currentContainer is set (an
  // indent-0 bullet), so a child appearing before ANY top-level bullet is caught by the orphan
  // check, but removing ONLY that check would still leave the file rejected by the separate
  // !sawTop guard below (sawTop never becomes true here either). Kept as a plain rejection proof,
  // like the mask-pairs — true isolation of the orphan-child branch is the heading-reset fixture above.
  const indexLines = ['  - child'];
  const result = wireNestedListChapter(indexLines, 'Admin', '[Items](admin/items.md)');
  assert.equal(result.kind, 'not-a-list');
});

test('not-a-list, bare-path guard [isolating, §5.7]: a "*"/"+"-marked bare (non-link) path row is refused, never silently left as a duplicate-risk phantom', () => {
  // step-0's bare-row fallback strips only "-", so "* admin/items.md" is invisible to it. WITH the
  // guard: refused outright. WITHOUT it: the row would parse as a normal plain-label indent-0
  // bullet ("admin/items.md" contains no denylisted char) that simply fails to match "Admin" -> the
  // real "Admin" container gets a new child spliced under it while this untouched phantom row stays
  // right where it was — a duplicate reference to the same real target step-0 can never see.
  const indexLines = ['- Admin', '* admin/items.md'];
  const result = wireNestedListChapter(indexLines, 'Admin', '[Items](admin/items.md)');
  assert.equal(result.kind, 'not-a-list');
});

test('not-a-list, args guard [isolating, groupTitle side]: a groupTitle carrying an embedded newline is refused, never spliced in as a foreign physical line', () => {
  const indexLines = ['- Admin'];
  const result = wireNestedListChapter(indexLines, 'Admin\nRogue Line', '[Items](admin/items.md)');
  assert.equal(result.kind, 'not-a-list');
});

test('not-a-list, args guard [isolating, chapterLink side]: a chapterLink carrying an embedded newline is refused too', () => {
  const indexLines = ['- Admin'];
  const result = wireNestedListChapter(indexLines, 'Admin', '[Items](admin/items.md)\nRogue Line');
  assert.equal(result.kind, 'not-a-list');
});

test('not-a-list, YAML guard [isolating]: "- Admin: path" (a mapping bullet) is refused even though it would ALSO pass the plain-bullet regex and label allowlist', () => {
  // This is what makes the fixture genuinely isolating (unlike a real "nav:" line, which fails the
  // bullet regex outright and is masked by step 6): "Admin: path" itself is a PLAIN label (":" is
  // allowed) — WITHOUT hasYamlMappingStructure's immediate guard, this line would be silently
  // accepted as an ordinary indent-0 bullet.
  const indexLines = ['- Admin: path'];
  const result = wireNestedListChapter(indexLines, 'Admin', '[Items](admin/items.md)');
  assert.equal(result.kind, 'not-a-list');
});

test('not-a-list, !sawTop [isolating]: a heading + blank lines only, never a real bullet, is refused rather than falling into a bogus create', () => {
  // ATX headings are ALLOWED lines (never step-6 foreign) — so WITHOUT the trailing !sawTop check,
  // this file would fall straight through to the ZERO-create branch with firstTopMarker still null,
  // producing a broken "null Admin" container line.
  const indexLines = ['# Summary', '', ''];
  const result = wireNestedListChapter(indexLines, 'Admin', '[Items](admin/items.md)');
  assert.equal(result.kind, 'not-a-list');
});

test('not-a-list, inconsistent child indent [isolating]: a second, DIFFERENT child indent under the same container is refused', () => {
  const indexLines = ['- Admin', '  - first', '   - second'];
  const result = wireNestedListChapter(indexLines, 'Admin', '[Items](admin/items.md)');
  assert.equal(result.kind, 'not-a-list');
});

test('not-a-list, foreign content [rejection, step 6]: a table row or a tab-prefixed line before an otherwise-clean list is refused', () => {
  assert.equal(wireNestedListChapter(['| A | B |', '- Admin'], 'Admin', '[Items](admin/items.md)').kind, 'not-a-list');
  assert.equal(wireNestedListChapter(['\tAdmin', '- Billing'], 'Billing', '[Items](admin/items.md)').kind, 'not-a-list');
});

// -------------------------------------------------------------------------------------------------
// Non-isolable mask-pairs (R3-7/R4-7) — test REJECTION, not isolation: step 6's foreign-content
// fallback backstops each of these, so removing the NAMED earlier guard alone does not flip the
// fixture to 'inserted' (a genuine remove-guard-flips-green isolation is impossible here).
// -------------------------------------------------------------------------------------------------

test('not-a-list, mask-pair: a stray "---" line is refused by step 2 (thematic break) AND, independently, would still fail the bullet regex at step 6', () => {
  const indexLines = ['- Admin', '---'];
  const result = wireNestedListChapter(indexLines, 'Admin', '[Items](admin/items.md)');
  assert.equal(result.kind, 'not-a-list');
});

test('not-a-list, mask-pair: a "1. Ordered" row is refused by step 3 (ordered marker) AND, independently, would still fail the bullet regex at step 6', () => {
  const indexLines = ['- Admin', '1. Ordered'];
  const result = wireNestedListChapter(indexLines, 'Admin', '[Items](admin/items.md)');
  assert.equal(result.kind, 'not-a-list');
});

test('not-a-list, mask-pair [R4-7]: a multiline HTML comment ("- Admin <!--" / "-->" / "- Other") is refused by the inert-identity guard AND, independently, its "-->" line would still be foreign at step 6', () => {
  // Unlike the isolating "`Admin`" fixture above, removing the identity guard here does NOT flip
  // this to 'inserted': the raw "-->" line fails the bullet/heading/thematic/ordered shapes on its
  // own, so step 6 rejects it regardless of the identity guard's presence.
  const indexLines = ['- Admin <!--', '-->', '- Other'];
  const result = wireNestedListChapter(indexLines, 'Admin', '[Items](admin/items.md)');
  assert.equal(result.kind, 'not-a-list');
});

test('not-a-list, mask-pair: a file mixing a CRLF-terminated line with a bare-LF-terminated line is refused by the mixed-EOL guard AND, independently, backstopped by the inert-identity guard', () => {
  // Empirically verified (not just argued): '- Admin\r' + '' + '' joins to '- Admin\r\n\n' — one
  // real CRLF boundary, then one bare LF. Removing JUST the mixed-EOL check does NOT flip this to
  // 'inserted' — the wrong EOL ('\r\n') splits the trailing bare '\n' into ITS OWN logical element
  // (a one-character string containing a literal '\n', not an empty string), and that embedded raw
  // newline desyncs the identity guard's own join('\n')/split('\n') round-trip (fm.join('\n') folds
  // the element's OWN '\n' together with the join separator, so splitting it back yields MORE
  // elements than fm has) — SAN[i] !== fm[i] fires independently. This is structural, not specific
  // to this fixture: any wrong-EOL split that leaves a bare LF embedded inside a logical element
  // will always desync that round-trip, so the mixed-EOL guard can never be independently isolated
  // from the inert-identity guard by ANY fixture of this shape.
  const indexLines = ['- Admin\r', '', ''];
  const result = wireNestedListChapter(indexLines, 'Admin', '[Items](admin/items.md)');
  assert.equal(result.kind, 'not-a-list');
});

// -------------------------------------------------------------------------------------------------
// [1.11.0] #330 prep — leadingFrontmatterSpan, the exported test-seam projection of the private
// prepareIndexLines (extracted out of wireNestedListChapter's own line-preparation pass into its
// own function — see prepareIndexLines's docstring for the step numbering. Step 4, the
// groupTitle/chapterLink embedded-newline guard, reads arguments this helper never receives and
// stays in the writer). Reaching these four rejection branches through the full writer proves
// nothing for three of them (a downstream guard masks the branch); reaching them directly through
// this seam is what actually isolates them — three discriminating direct tests plus one masked
// rejection test, not one protected test per branch (plan round-30).
// -------------------------------------------------------------------------------------------------

test('leadingFrontmatterSpan [isolating]: an unclosed leading "---" block is refused directly through this seam (through the full writer it is masked by !sawTop, so only this seam proves the guard)', () => {
  const indexLines = ['---', 'description: x'];
  assert.equal(leadingFrontmatterSpan(indexLines).kind, 'not-a-list');
});

test('leadingFrontmatterSpan [isolating]: a backtick code-span on a CHILD bullet desyncs the identity guard — unlike the existing container-label fixture (masked by isPlainLabel, which only ever sees indent-0 labels and group_title), a child\'s content is never run through isPlainLabel at all', () => {
  const indexLines = ['- Admin', '  - `child`'];
  assert.equal(leadingFrontmatterSpan(indexLines).kind, 'not-a-list');
});

test('leadingFrontmatterSpan [masked by the identity guard, per the mask-pair fixture above]: a mixed-EOL file also returns not-a-list through this seam, but the mixed-EOL guard itself cannot be isolated by any fixture of this shape', () => {
  const indexLines = ['- Admin\r', '', ''];
  assert.equal(leadingFrontmatterSpan(indexLines).kind, 'not-a-list');
});

test('leadingFrontmatterSpan [isolating, permanent — round-10 probe]: a lone CR not part of a CRLF pair is refused (nothing else in this helper catches a bare interior \\r)', () => {
  const indexLines = ['- Admin', '\r'];
  assert.equal(leadingFrontmatterSpan(indexLines).kind, 'not-a-list');
});

test('leadingFrontmatterSpan: no leading frontmatter => span is null', () => {
  assert.deepEqual(leadingFrontmatterSpan(['- Admin', '  - guide/items.md']), { kind: 'ok', span: null });
});

test('leadingFrontmatterSpan: a closed "---"/"---" frontmatter block reports the exact blanked span', () => {
  const indexLines = ['---', 'title: X', '---', '- Admin'];
  assert.deepEqual(leadingFrontmatterSpan(indexLines), { kind: 'ok', span: { start: 0, endExclusive: 3 } });
});

test('leadingFrontmatterSpan: a "..." document-end terminator closes the frontmatter block too, same span shape as "---"', () => {
  const indexLines = ['---', 'title: X', '...', '- Admin'];
  assert.deepEqual(leadingFrontmatterSpan(indexLines), { kind: 'ok', span: { start: 0, endExclusive: 3 } });
});

// -------------------------------------------------------------------------------------------------
// Positive-accept fixtures — guard against over-rejection (a mutant that is TOO strict must also fail)
// -------------------------------------------------------------------------------------------------

test('positive-accept: closed frontmatter with an interior block-scalar "  ---" plus a real column-0 "---" closer is accepted, and the raw frontmatter survives untouched in the output', () => {
  // A mutant reverting to the module's trimmed '.trim()===\'---\'' closer test would mis-read the
  // INDENTED block-scalar line as the closer (falsely rejecting a clean file) — the real, robust
  // closer is an EXACT, untrimmed column-0 equality check that only the true closer (line index 4)
  // satisfies.
  const indexLines = ['---', 'description: |', '  ---', '  more scalar text', '---', '', '- Admin'];
  const result = wireNestedListChapter(indexLines, 'Admin', '[Items](admin/items.md)');
  assert.equal(result.kind, 'inserted');
  assert.deepEqual(result.newLines, [
    '---',
    'description: |',
    '  ---',
    '  more scalar text',
    '---',
    '',
    '- Admin',
    '  - [Items](admin/items.md)',
  ]);
});

test('positive-accept: a CRLF file with NO terminal newline round-trips exactly — interior \\r\\n preserved, no trailing bare \\r (mutant: a per-element \\r patch would corrupt this)', () => {
  const indexLines = ['- Admin\r', '- Billing'];
  const result = wireNestedListChapter(indexLines, 'Billing', '[Items](billing/items.md)');
  assert.equal(result.kind, 'inserted');
  assert.deepEqual(result.newLines, ['- Admin\r', '- Billing\r', '  - [Items](billing/items.md)']);
  assert.equal(result.newLines.join('\n'), '- Admin\r\n- Billing\r\n  - [Items](billing/items.md)');
  assert.ok(
    !result.newLines[result.newLines.length - 1].endsWith('\r'),
    'the final (non-terminated) line must not gain a trailing bare \\r',
  );
});

// -------------------------------------------------------------------------------------------------
// [1.11.0] #330 prep — prepareIndexLines parity, through the writer's own newLines output. The
// pre-existing suite never exercised the "..." terminator or a CRLF file carrying leading
// frontmatter, so its own green run certifies nothing for those branches of the moved code
// (plan Tests item 1 / round-24). Each expected newLines value below was measured against the
// real module, not hand-derived.
// -------------------------------------------------------------------------------------------------

test('positive-accept, parity: a frontmatter block closed with "..." (not "---") is accepted and survives untouched in the output', () => {
  const indexLines = ['---', 'title: X', '...', '- Admin'];
  const result = wireNestedListChapter(indexLines, 'Admin', '[Items](admin/items.md)');
  assert.equal(result.kind, 'inserted');
  assert.deepEqual(result.newLines, ['---', 'title: X', '...', '- Admin', '  - [Items](admin/items.md)']);
});

test('positive-accept, parity: CRLF file WITH leading frontmatter, no terminal newline — round-trips exactly', () => {
  const indexLines = ['---\r', 'title: X\r', '---\r', '- Admin'];
  const result = wireNestedListChapter(indexLines, 'Admin', '[Items](admin/items.md)');
  assert.equal(result.kind, 'inserted');
  assert.deepEqual(result.newLines, ['---\r', 'title: X\r', '---\r', '- Admin\r', '  - [Items](admin/items.md)']);
});

test('positive-accept, parity: CRLF file WITH leading frontmatter AND a terminal newline — round-trips exactly, including the final empty element', () => {
  const indexLines = ['---\r', 'title: X\r', '---\r', '- Admin\r', ''];
  const result = wireNestedListChapter(indexLines, 'Admin', '[Items](admin/items.md)');
  assert.equal(result.kind, 'inserted');
  assert.deepEqual(result.newLines, ['---\r', 'title: X\r', '---\r', '- Admin\r', '  - [Items](admin/items.md)\r', '']);
});

test('positive-accept, padded group_title on CREATE [R4-4]: the emitted container is the exactly-trimmed label, never the raw padded value', () => {
  const indexLines = ['- Intro'];
  const result = wireNestedListChapter(indexLines, '  Admin  ', '[Items](admin/items.md)');
  assert.equal(result.kind, 'inserted');
  assert.equal(result.created, true);
  assert.deepEqual(result.newLines, ['- Intro', '- Admin', '  - [Items](admin/items.md)']);
});

test('positive-accept: a padded group_title also converges against an EXISTING plain container (trimmed match, not just trimmed create)', () => {
  const indexLines = ['- Admin'];
  const result = wireNestedListChapter(indexLines, '  Admin  ', '[Items](admin/items.md)');
  assert.equal(result.kind, 'inserted');
  assert.equal(result.created, false, 'must match the existing container, not create a duplicate');
});

// -------------------------------------------------------------------------------------------------
// extractLabel / isPlainLabel — DIRECT unit tests (exported per R5-4). String literals below use
// DOUBLED backslashes so a real Markdown backslash-escape survives into the JS string value being
// tested (a single backslash in the .mjs source literal is a JS escape, not a Markdown one).
// -------------------------------------------------------------------------------------------------

test('extractLabel: escape-aware whole-content link unwrap (an escaped "]" inside the label does not close it early)', () => {
  assert.equal(extractLabel('[Plans \\[Beta\\]](p.md)'), 'Plans [Beta]');
});

test('extractLabel: surrounding prose prevents a false whole-content unwrap (the "[" is not at position 0)', () => {
  assert.equal(extractLabel('See [Admin](a.md)'), 'See [Admin](a.md)');
});

test('extractLabel: whole-content wikilink with an alias returns the alias', () => {
  assert.equal(extractLabel('[[t|alias]]'), 'alias');
});

test('extractLabel: whole-content wikilink with no alias returns the target', () => {
  assert.equal(extractLabel('[[t]]'), 't');
});

test('extractLabel: bare text is returned trimmed, verbatim', () => {
  assert.equal(extractLabel('  Just text  '), 'Just text');
});

test('isPlainLabel: ordinary plain strings are accepted (interior hyphen, dot, parens, single interior space)', () => {
  assert.equal(isPlainLabel('Admin'), true);
  assert.equal(isPlainLabel('A - B'), true);
  assert.equal(isPlainLabel('v1.2'), true);
  assert.equal(isPlainLabel('a (b)'), true);
});

test('isPlainLabel: every inline-active char / leading block trigger / whitespace-collapse construct is rejected', () => {
  assert.equal(isPlainLabel('**Admin**'), false, 'emphasis asterisks');
  assert.equal(isPlainLabel('Admin\\!'), false, 'raw backslash escape');
  assert.equal(isPlainLabel('A & B'), false, 'entity ampersand');
  assert.equal(isPlainLabel('![x]'), false, 'image bang+bracket');
  assert.equal(isPlainLabel('~x~'), false, 'strikethrough tilde');
  assert.equal(isPlainLabel('a_b_'), false, 'underscore anywhere is rejected, not just a flanking pair');
  assert.equal(isPlainLabel('`Admin`'), false, 'backtick code-span delimiter (R2 regression)');
  assert.equal(isPlainLabel('Admin'), true, 'a normal plain label with no backtick stays accepted');
});

test('public match: an allowlist-clean whole-content link matches its groupTitle through the public wireNestedListChapter API', () => {
  const indexLines = ['- [Getting Started](gs.md)'];
  const result = wireNestedListChapter(indexLines, 'Getting Started', '[Setup](gs/setup.md)');
  assert.equal(result.kind, 'inserted');
  assert.equal(result.created, false, 'the existing "Getting Started" bullet is reused, not re-created');
});

test('public match: "See [Admin](a.md)" never falsely matches groupTitle "Admin" — extractLabel refuses the false unwrap AND the raw bracketed label independently fails isPlainLabel (a STRONGER outcome than a bare non-match: the file is refused outright, not routed to a ZERO create)', () => {
  // Two independent safeguards compose here rather than one masking a gap in the other: even if
  // extractLabel unwrapped more aggressively, isPlainLabel would still refuse the resulting raw
  // label (it carries '[' ']'); even if isPlainLabel's char denylist were narrower, extractLabel's
  // refusal to whole-content-unwrap a non-whole-content string already prevents the false match.
  const indexLines = ['- See [Admin](a.md)'];
  const result = wireNestedListChapter(indexLines, 'Admin', '[Items](admin/items.md)');
  assert.equal(result.kind, 'not-a-list');
});

test('wireNestedListChapter: a non-string groupTitle (42, null, undefined) never throws — returns a typed result, matching the sibling findContainer contract (R1 MINOR)', () => {
  for (const badTitle of [42, null, undefined]) {
    assert.doesNotThrow(() => {
      const result = wireNestedListChapter(['- Admin'], badTitle, 'x');
      assert.equal(typeof result.kind, 'string', `expected a typed result for groupTitle ${String(badTitle)}`);
    }, `wireNestedListChapter must not throw for groupTitle ${String(badTitle)}`);
  }
});

// -------------------------------------------------------------------------------------------------
// Purity
// -------------------------------------------------------------------------------------------------

test('wireNestedListChapter is pure: a frozen input array is never mutated, and the output is always a fresh array reference', () => {
  const frozen = Object.freeze(['- Admin']);
  const result = wireNestedListChapter(frozen, 'Admin', '[Items](admin/items.md)');
  assert.equal(result.kind, 'inserted');
  assert.deepEqual(frozen, ['- Admin'], 'the frozen input array must be byte-for-byte unchanged');
  assert.notEqual(result.newLines, frozen, 'the returned newLines must be a distinct array reference');
});

// -------------------------------------------------------------------------------------------------
// [1.11.0] #330 prep — containerOwnerScan extraction (the writer's forward pass lifted, unchanged,
// into a private helper — plan round-21 HIGH). PRIVATE, no exported test seam, so these two tests
// characterize it through wireNestedListChapter. The extraction's contract is that NO consumer
// re-implements this scan — each one calls it — so consumers cannot disagree about which container
// owns a line, however many consumers there are (today: wireNestedListChapter here, and
// verifyNonHeadingPlacement below, whose section also covers the ownerOf/ownerLabelOf fields
// wireNestedListChapter never reads). The existing MULTIPLE fixture above never involves a heading;
// this one pins that `containers` collection is independent of the heading-driven currentContainer
// reset (only child OWNERSHIP resets on a heading, never label-matching membership).
// -------------------------------------------------------------------------------------------------

test('wireNestedListChapter MULTIPLE, heading-independent: two same-label indent-0 bullets split by an ATX heading still BOTH count', () => {
  const indexLines = ['- Admin', '# Section', '- Admin', '  - guide/items.md'];
  const result = wireNestedListChapter(indexLines, 'Admin', '[Items](guide/items.md)');
  assert.equal(result.kind, 'multiple');
  assert.deepEqual(result.matches, [
    { index: 0, label: 'Admin' },
    { index: 2, label: 'Admin' },
  ]);
});

test('wireNestedListChapter, repeat-invocation isolation: a SINGLE call that populates a matched container, followed by an unrelated ZERO-create call, leaks no scan state between calls', () => {
  // The extraction gave the forward pass its own function-call boundary (containerOwnerScan) —
  // this pins that `containers` (and the rest of the scan's locals) are call-scoped, not
  // accidentally hoisted to module scope. The pairing matters: the FIRST call must actually
  // populate `containers` (a leaked, un-cleared array would silently corrupt the SECOND call,
  // which expects to see none).
  const first = wireNestedListChapter(['- Admin', '  - existing.md'], 'Admin', '[Items](admin/items.md)');
  assert.equal(first.kind, 'inserted');
  assert.equal(first.created, false);
  const second = wireNestedListChapter(['- Intro'], 'Admin', '[Items](admin/items.md)');
  assert.equal(second.kind, 'inserted');
  assert.equal(second.created, true, 'the second call must see NO matched container, unaffected by the first call');
  assert.deepEqual(second.newLines, ['- Intro', '- Admin', '  - [Items](admin/items.md)']);
});

// =================================================================================================
// [1.11.0] #330 — verifyNonHeadingPlacement (present-line placement verification, nested-list form)
// =================================================================================================
//
// verifyNonHeadingPlacement is IMPLEMENTED (EH-CORE, commit 419ef4c). It reaches the container walk
// through containerOwnerScan rather than re-deriving it, which is the whole point of the extraction
// above: every consumer answers "which container owns this line" by calling the one scan, so no two
// of them can drift apart (today: wireNestedListChapter and this — however many there come to be).
// E.g. verifyNonHeadingPlacement(['- Admin', '  - guide/items.md'], 'guide/items.md', 'Admin')
// returns {kind: 'ok'}, pinned below by the test named "verifyNonHeadingPlacement rule 5: a
// correctly-nested child under its matching container -> ok". Every fixture's PRECONDITION — match
// cardinality, frontmatter span, and the fixed-probe predicate's kind — was driven through the real
// supporting helpers (locateChapterLine/indexView, leadingFrontmatterSpan, wireNestedListChapter)
// BEFORE the implementation landed, so nothing here rests on an unmeasured setup. Every new gate's
// [isolating]/[masked] label was then confirmed by a scoped guard-mutation run (Edit-revert,
// RED-before-green) against the landed implementation — see each fixture's own comment for its
// specific result.
//
// The five-rule decision table (plan "Decision order is fixed"), in order: 1. zero selected-target
// matches -> inconsistent; 2. more than one match -> inconsistent; 3. the single match lies inside
// the leading frontmatter span -> unverifiable; 4. the fixed-probe predicate declines (not-a-list
// or multiple) -> unverifiable; 5. otherwise compare the container -> ok / misplaced(label|null).
// Every "-> unverifiable" fixture below is scoped by the SINGLE-MATCH precondition: rules 1-2
// already remove every wrong-cardinality file before rules 3-5 ever run.

test('verifyNonHeadingPlacement rule 1 [isolating, mutation-confirmed]: ZERO selected-target matches -> inconsistent, even though the file is an otherwise-perfectly-formed container', () => {
  // Mutation-confirmed: narrowing the cardinality guard (`const { matches } =
  // locateChapterLine(...); if (matches.length !== 1) ...`) to `matches.length > 1` (dropping
  // rule 1's own half) flips ONLY this fixture; every other fixture in this suite, including the
  // rule-2 ones just below, stays green.
  const result = verifyNonHeadingPlacement(['- Admin'], 'guide/items.md', 'Admin');
  assert.deepEqual(result, { kind: 'inconsistent' });
});

test('verifyNonHeadingPlacement rule 2 [isolating, mutation-confirmed]: TWO selected-target matches (a flat file with the target repeated) -> inconsistent, not "pick the first"', () => {
  // Measured (plan round-14/round-9 HIGH 1): the writer sees ONE matching container ("Admin") and
  // returns inserted/created:false, while the locator independently reports 2 matches for the
  // selected target — rule 2 must fire on TARGET cardinality regardless of the predicate's own answer.
  // Mutation-confirmed: narrowing that same cardinality guard to `matches.length < 1` (dropping
  // rule 2's own half) flips ONLY this fixture and the monotonicity fixture below.
  const indexLines = ['- Admin', '- guide/items.md', '- Other', '- guide/items.md'];
  const result = verifyNonHeadingPlacement(indexLines, 'guide/items.md', 'Admin');
  assert.deepEqual(result, { kind: 'inconsistent' });
});

test('verifyNonHeadingPlacement rule 3 [isolating, mutation-confirmed]: a single match lying inside a closed leading frontmatter block -> unverifiable', () => {
  // Measured: leadingFrontmatterSpan reports span {start:0, endExclusive:4} for this file, and the
  // ONLY occurrence of "guide/items.md" (index 2) lies inside it — indexView does not blank
  // frontmatter, so locateChapterLine still reports present:true, single match. Mutation-confirmed:
  // disabling just the frontmatter-span check flips ONLY this fixture (to misplaced(null), since
  // the blanked container line then has no owner) — no other fixture in this suite depends on it.
  const indexLines = ['---', '- Admin', '  - guide/items.md', '---', '- Admin'];
  const result = verifyNonHeadingPlacement(indexLines, 'guide/items.md', 'Admin');
  assert.deepEqual(result, { kind: 'unverifiable' });
});

test('verifyNonHeadingPlacement monotonicity [round-15/16 HIGH]: a match BOTH inside AND outside frontmatter is inconsistent (rule 2), never the softer unverifiable (rule 3) — this is what forced rules 2 and 3 to swap order', () => {
  // Measured: 2 matches total (one inside the {0,4} frontmatter span, one outside) -> rule 2 fires
  // before rule 3 is ever consulted. Watched against the pre-swap order this must flip to
  // unverifiable, proving the reorder is load-bearing, not cosmetic.
  const indexLines = ['---', '- Admin', '  - guide/items.md', '---', '- Admin', '  - guide/items.md'];
  const result = verifyNonHeadingPlacement(indexLines, 'guide/items.md', 'Admin');
  assert.deepEqual(result, { kind: 'inconsistent' });
});

test('verifyNonHeadingPlacement evaluation-order precondition [round-27 IMPORTANT] [isolating, mutation-confirmed]: a single match plus a trailing lone CR (prepareIndexLines declines) -> unverifiable, not a crash reading span/body off a refusal', () => {
  // Measured: locateChapterLine still finds exactly ONE match (indexView has no CR-awareness), but
  // prepareIndexLines refuses the file outright (the lone-CR guard) before span/body ever exist —
  // the verifier must check prep.kind before touching either field. The writer would decline this
  // file identically, so this is not a sixth rule: it is the same rule-4 outcome reached one step
  // earlier because rule 3's own precondition (a real span) is unavailable. Mutation-confirmed:
  // disabling this precondition check flips this fixture too — with `prep.span` undefined on a
  // refusal, the frontmatter check itself throws reading `.start` off `undefined`, exactly the
  // crash this precondition exists to prevent. It ALSO flips the "<!--nav-->" marker fixture below
  // (the two share this exact masking site, confirmed by running both mutants).
  const indexLines = ['- Admin', '  - guide/items.md', '\r'];
  const result = verifyNonHeadingPlacement(indexLines, 'guide/items.md', 'Admin');
  assert.deepEqual(result, { kind: 'unverifiable' });
});

// -------------------------------------------------------------------------------------------------
// Rule 4 — the fixed-probe predicate declines. Each fixture is a distinct "nav-form" shape an
// operator actually hits (plan's automation-limits enumeration), each built with EXACTLY ONE
// selected-target match so the fixture exercises rule 4, never rule 1.
// -------------------------------------------------------------------------------------------------

test('verifyNonHeadingPlacement rule 4, native/YAML config [round-9 HIGH 2] [masked by containerOwnerScan\'s own foreign-content guard]: a fully spaced-colon "site_name : / nav : / - Admin :" file escapes the YAML-mapping detector entirely and is STILL refused (foreign content), not falsely verified', () => {
  // This is the fixture that closes the native/YAML-config case where a text-based YAML detector
  // cannot: YAML_MAPPING_LINE_RE requires the colon to touch the key, so every line here defeats it
  // — yet the shape predicate still declines (measured: wireNestedListChapter returns not-a-list),
  // because "site_name : Handbook" is foreign content on its own (no marker, no ATX heading).
  // Mutation-confirmed MASKED, not isolating: disabling rule 4's own decline check does NOT flip
  // this fixture, because rule 5's OWN containerOwnerScan(prep.body, wanted) call independently
  // rejects "site_name : Handbook" as foreign content before ever reaching a container comparison —
  // per this file's mask-pair convention, documented rather than claimed isolating.
  const indexLines = ['site_name : Handbook', 'nav :', '- Admin :', '  - Items : guide/items.md'];
  const result = verifyNonHeadingPlacement(indexLines, 'guide/items.md', 'Admin');
  assert.deepEqual(result, { kind: 'unverifiable' });
});

test('verifyNonHeadingPlacement rule 4, typed-key nav row ["- Yes:" case] [isolating, mutation-confirmed]: a bare YAML-mapping key ("- Yes:") declines, closing the boolean-vs-string ambiguity a text comparator cannot resolve safely', () => {
  // The plan's own illustration: Psych would parse the key as boolean `true` while a text rule
  // reads the string "Yes" — a text-based container comparator would falsely say ok. Delegating the
  // whole shape to the writer's hasYamlMappingStructure guard sidesteps the ambiguity outright.
  // Mutation-confirmed isolating (unlike the mkdocs.yml fixture above): "- Yes:" IS a perfectly
  // valid plain-label indent-0 bullet as far as containerOwnerScan is concerned (isPlainLabel("Yes:")
  // is true) — hasYamlMappingStructure is a WRITER-only pre-loop guard containerOwnerScan never
  // runs, so disabling rule 4's own check genuinely flips this fixture (to misplaced('Yes:'), not a
  // crash or a mask) while the mkdocs.yml fixture above stays caught by a different guard.
  const indexLines = ['- Yes:', '  - Items: guide/items.md'];
  const result = verifyNonHeadingPlacement(indexLines, 'guide/items.md', 'Yes');
  assert.deepEqual(result, { kind: 'unverifiable' });
});

test('verifyNonHeadingPlacement rule 4, non-plain groupTitle [EH-CORE finding, isolating, mutation-confirmed]: a construct-bearing groupTitle ("`Admin`", backtick-wrapped) declines through the WRITER-only isPlainLabel(wanted) pre-loop guard, which containerOwnerScan itself never checks', () => {
  // This is a DIFFERENT guard from the "- Yes:" fixture above: isPlainLabel(wanted) tests the
  // groupTitle PARAMETER itself (chapter-paths.mjs, wireNestedListChapter's own pre-loop check),
  // not any label already present in the file — containerOwnerScan's own isPlainLabel call only
  // ever tests a FILE bullet's `info.label`, never `wanted`, so it has no reach over a malformed
  // caller-supplied groupTitle at all. EH-CORE measured (relayed by the lead) that disabling rule
  // 4 entirely turns this into a WRONG `misplaced` rather than `unverifiable` — confirmed here
  // directly: with rule 4's decline check disabled, containerOwnerScan(prep.body, '`Admin`') runs
  // to completion undisturbed (the file's own "Admin" label is perfectly plain), and the comparison
  // against the malformed wantedLabel never matches, producing misplaced('Admin') instead of the
  // correct unverifiable. Genuinely isolating: unlike the mkdocs.yml/ordered-list/wildcard/table
  // fixtures above, nothing in containerOwnerScan itself ever inspects `wanted` for plainness.
  const indexLines = ['- Admin', '  - guide/items.md'];
  const result = verifyNonHeadingPlacement(indexLines, 'guide/items.md', '`Admin`');
  assert.deepEqual(result, { kind: 'unverifiable' });
});

test('verifyNonHeadingPlacement rule 4, literate-nav ordered list [masked by containerOwnerScan\'s own ordered-marker guard]: "1. [Admin](admin.md)" is an ordinary literate-nav feature the writer declines (ordered marker), not a container', () => {
  // Mutation-confirmed MASKED: disabling rule 4's own decline check does not flip this fixture —
  // containerOwnerScan's own NESTED_ORDERED_MARKER_RE guard rejects the ordered-list line
  // independently, the same shared-scan mechanism rule 5 would call anyway.
  const indexLines = ['1. [Admin](admin.md)', '   - [Items](guide/items.md)'];
  const result = verifyNonHeadingPlacement(indexLines, 'guide/items.md', 'Admin');
  assert.deepEqual(result, { kind: 'unverifiable' });
});

test('verifyNonHeadingPlacement rule 4, literate-nav wildcard [masked by containerOwnerScan\'s own bare-path guard]: "* subdirectory/*.md" is refused by the bare-path guard, not silently treated as a container', () => {
  // Mutation-confirmed MASKED, same mechanism as the ordered-list fixture above: containerOwnerScan
  // itself refuses this line via isBarePathBullet before any container comparison would run.
  const indexLines = ['* subdirectory/*.md', '- [Items](guide/items.md)'];
  const result = verifyNonHeadingPlacement(indexLines, 'guide/items.md', 'Admin');
  assert.deepEqual(result, { kind: 'unverifiable' });
});

test('verifyNonHeadingPlacement rule 4, "<!--nav-->" marker file [masked by the EARLIER prep.kind precondition, not by rule 4 itself]: an inert-content marker line desyncs the identity guard (same mechanism as a stray backtick), so this shape declines too', () => {
  // Measured: leadingFrontmatterSpan ALSO reports not-a-list for this file — the marker line is
  // blanked by stripInertContexts while the raw line survives comparison, tripping the identity
  // guard directly (not the foreign-content guard). Mutation-confirmed: disabling rule 4's own
  // decline check does NOT flip this fixture (prepareIndexLines already refused it upstream); it
  // DOES flip together with the lone-CR precondition fixture above when THAT earlier check is
  // disabled instead — same masking site, confirmed by running both mutants independently.
  const indexLines = ['<!--nav-->', '- [Items](guide/items.md)'];
  const result = verifyNonHeadingPlacement(indexLines, 'guide/items.md', 'Admin');
  assert.deepEqual(result, { kind: 'unverifiable' });
});

test('verifyNonHeadingPlacement rule 4, exotic path-table row [masked by containerOwnerScan\'s own foreign-content guard]: a pipe-table cell carrying a markdown link is recognized by the locator but the row itself is foreign content to the writer', () => {
  const indexLines = ['| [Items](guide/items.md) |'];
  const result = verifyNonHeadingPlacement(indexLines, 'guide/items.md', 'Admin');
  assert.deepEqual(result, { kind: 'unverifiable' });
});

test('verifyNonHeadingPlacement rule 4, "multiple" is mapped explicitly [round-9 HIGH 1] [isolating, mutation-confirmed]: TWO indent-0 containers sharing group_title, but exactly ONE selected-target match under one of them, still resolves to unverifiable — not ok, not misplaced', () => {
  // Isolates the `multiple` mapping from the cardinality rules: matches.length===1 (rules 1/2 pass)
  // yet the predicate itself returns {kind:'multiple'} (container-ambiguous), which rule 4 must
  // treat exactly like not-a-list. Mapping only not-a-list here would make the verifier MORE
  // permissive than the writer on an ambiguous file — the unsafe direction. Mutation-confirmed:
  // disabling rule 4's own decline check flips this to a false `ok` — unlike the fixtures above,
  // containerOwnerScan itself has NO concept of "too many matching containers" (that ambiguity is
  // the WRITER's own post-scan `containers.length >= 2` check, outside the shared scan), so nothing
  // downstream masks the removal.
  const indexLines = ['- Admin', '- Admin', '  - guide/items.md'];
  const result = verifyNonHeadingPlacement(indexLines, 'guide/items.md', 'Admin');
  assert.deepEqual(result, { kind: 'unverifiable' });
});

test('verifyNonHeadingPlacement rule 4, heading + INDENTED child [round-20 panel, reproduced] [masked by containerOwnerScan\'s own orphan-child guard]: the writer refuses this at its own orphan-child guard, so the file reaches rule 4, NEVER rule 5 — a heading reset cannot be walked around', () => {
  // The plan's own correction of an earlier (wrong) credit: a naive nearest-preceding-container scan
  // would call this misplaced(null), but the real predicate declines the file outright (not-a-list)
  // before any container comparison happens, so the correct answer here is unverifiable.
  // Mutation-confirmed MASKED: disabling rule 4's own check does not flip this fixture, since rule
  // 5's own containerOwnerScan(prep.body, wanted) call hits the SAME orphan-child guard independently
  // (it is the identical shared function) — a heading reset genuinely cannot be walked around from
  // either call site.
  const indexLines = ['- Admin', '# Section', '  - guide/items.md'];
  const result = verifyNonHeadingPlacement(indexLines, 'guide/items.md', 'Admin');
  assert.deepEqual(result, { kind: 'unverifiable' });
});

// -------------------------------------------------------------------------------------------------
// Rule 5 — the container comparison. `misplaced` carries `foundContainer` (a label string, or null
// when the matched row is uncontained). Every fixture below has a predicate that ACCEPTS the file
// (kind:'inserted'), so rule 4 always passes through to the container walk.
// -------------------------------------------------------------------------------------------------

test('verifyNonHeadingPlacement rule 5: a correctly-nested child under its matching container -> ok', () => {
  const indexLines = ['- Admin', '  - guide/items.md'];
  const result = verifyNonHeadingPlacement(indexLines, 'guide/items.md', 'Admin');
  assert.deepEqual(result, { kind: 'ok' });
});

test('verifyNonHeadingPlacement rule 5, uncontained [round-15 HIGH] [isolating, mutation-confirmed]: a TOP-LEVEL sibling ("- guide/items.md" at indent 0) is misplaced(null), not ok — the writer treats every indent-0 bullet as its OWN container, never a child', () => {
  // Mutation-confirmed: narrowing the "owner === -1 || owner === undefined" guard to
  // "owner === undefined" only flips this fixture (and the heading-reset one below) to
  // misplaced(undefined) instead of misplaced(null) — the -1 sentinel (a bullet is its own
  // container, never a child) is genuinely load-bearing, not redundant with the undefined case.
  const indexLines = ['- Admin', '- guide/items.md'];
  const result = verifyNonHeadingPlacement(indexLines, 'guide/items.md', 'Admin');
  assert.deepEqual(result, { kind: 'misplaced', foundContainer: null });
});

test('verifyNonHeadingPlacement rule 5, uncontained after a heading reset [round-15 HIGH] [isolating, mutation-confirmed — same mutant as the plain-sibling fixture above]: a top-level sibling AFTER an ATX heading is still misplaced(null), same as the plain-sibling case', () => {
  const indexLines = ['- Admin', '# Section', '- guide/items.md'];
  const result = verifyNonHeadingPlacement(indexLines, 'guide/items.md', 'Admin');
  assert.deepEqual(result, { kind: 'misplaced', foundContainer: null });
});

test('verifyNonHeadingPlacement rule 5, misplaced under a genuinely different container [isolating, mutation-confirmed — see the containerOwnerScan-coverage section below for the paired mutant]: a child correctly nested under "- Other" while groupTitle is "Admin" -> misplaced(\'Other\') — the writer\'s created:true CREATE-a-new-container branch is exactly this case, not unverifiable', () => {
  // Measured: containers.length===0 ('Other' !== 'Admin'), so the predicate itself returns
  // inserted/created:true (it would CREATE a new "Admin" container) — proving created:true stays in
  // the recognized/misplaced class rather than being folded into unverifiable alongside `multiple`.
  const indexLines = ['- Other', '  - guide/items.md'];
  const result = verifyNonHeadingPlacement(indexLines, 'guide/items.md', 'Admin');
  assert.deepEqual(result, { kind: 'misplaced', foundContainer: 'Other' });
});

test('verifyNonHeadingPlacement rule 5, container label comparison is UNTRIMMED [round-18 panel, mutation guard] [isolating, mutation-confirmed]: an existing "[ Admin ](admin.md)" container (extractLabel returns the untrimmed " Admin ") does not match a trimmed "Admin" groupTitle -> misplaced(\' Admin \'), never ok', () => {
  // WITH the no-trim comparison (the writer's own containers.length===0 branch already proves
  // " Admin " !== "Admin" is exactly how the writer itself reads this file — created:true). WITHOUT
  // it (a mutant that trims ownerLabelOf before comparing): this fixture would wrongly flip to ok,
  // even though the writer, asked with the SAME groupTitle, says no matching container exists.
  // Mutation-confirmed: adding `.trim()` to the ownerLabel comparison flips ONLY this fixture — every
  // other rule-5 fixture in this suite has an already-untrimmed label, so trimming is a no-op there.
  const indexLines = ['- [ Admin ](admin.md)', '  - guide/items.md'];
  const result = verifyNonHeadingPlacement(indexLines, 'guide/items.md', 'Admin');
  assert.deepEqual(result, { kind: 'misplaced', foundContainer: ' Admin ' });
});

test('verifyNonHeadingPlacement rule 5, CRLF regression [round-18 panel] [isolating, mutation-confirmed]: a correctly-wired CRLF index -> ok, watched failing against a mutant that swaps the shared container walk for a walk over indexView instead of the writer\'s own BODY', () => {
  // Mutation-confirmed: swapping containerOwnerScan's array argument from prep.body to
  // indexView(indexLines) flips ONLY this fixture and the codex frontmatter+comment fixture below —
  // every other rule-5 "ok" fixture (the plain child, the compatibility matrix, ...) has no
  // BODY/indexView divergence and stays green under that same mutant.
  const indexLines = ['- Admin\r', '  - guide/items.md'];
  const result = verifyNonHeadingPlacement(indexLines, 'guide/items.md', 'Admin');
  assert.deepEqual(result, { kind: 'ok' });
});

test('verifyNonHeadingPlacement rule 5, codex frontmatter+HTML-comment regression [round-18/19 panel — the decisive counterexample against walking indexView] [isolating, mutation-confirmed — see the CRLF fixture above for the paired mutant]: a chapter freshly wired by the writer into a file whose frontmatter contains an HTML comment spanning into the body -> ok, never a false misplaced(null)', () => {
  // The writer accepts this file and reports inserted/created:false — by construction it agrees the
  // inserted row IS correctly nested under "Admin". But indexView (unlike the writer's own BODY)
  // sanitizes the WHOLE raw text with no frontmatter awareness: the "<!--" opened inside frontmatter
  // runs all the way to the body's "-->", which BLANKS the "- Admin" container line while leaving the
  // freshly-inserted chapter row (after the comment closes) untouched and visible. A container walk
  // over indexView would therefore find no container at all for a row the writer itself just wired —
  // the exact "second recognizer" drift the shared containerOwnerScan/BODY design exists to prevent.
  const written = wireNestedListChapter(
    ['---', 'note: <!--', '---', '- Admin', '  - -->'],
    'Admin',
    '[Items](admin/items.md)',
  );
  assert.equal(written.kind, 'inserted');
  assert.equal(written.created, false);
  const result = verifyNonHeadingPlacement(written.newLines, 'admin/items.md', 'Admin');
  assert.deepEqual(result, { kind: 'ok' });
});

// -------------------------------------------------------------------------------------------------
// Direct coverage of containerOwnerScan's `ownerOf`/`ownerLabelOf` fields (round-22 HIGH), reached
// through verifyNonHeadingPlacement as their first real consumer — EH-CORE's own characterization of
// these two fields used a temporary scratch export that was deleted before landing, so nothing in
// the committed suite exercised them directly until these three fixtures.
// -------------------------------------------------------------------------------------------------

test('verifyNonHeadingPlacement, containerOwnerScan coverage [isolating, mutation-confirmed]: TWO containers where only ONE matches groupTitle — the NON-matching container\'s own (untrimmed) label is still recorded as the owner label for ITS child, not left undefined', () => {
  // "Admin" is the only container pushed into `containers` (the writer\'s own match set), yet the
  // second container "Other" — which never enters that array — must still hand its OWN label to
  // ownerLabelOf for the child underneath it. If ownerLabelOf were (wrongly) derived FROM the
  // `containers` array instead of tracked independently, this fixture would misplaced(undefined) or
  // throw rather than correctly naming "Other". Mutation-confirmed: replacing containerOwnerScan's
  // per-line `ownerLabelOf[i] = currentContainerLabel` with `ownerLabelOf[i] = wanted` (deriving the
  // label from the match target instead of tracking it) flips this fixture to a false `ok` — along
  // with the heading-reset fixture below, the plain "misplaced under Other" fixture above, and the
  // untrimmed-label fixture above; every ALREADY-`ok` fixture in the suite is unaffected (the mutant
  // is a no-op whenever the real owner label already equals `wanted`).
  const indexLines = ['- Admin', '  - a.md', '- Other', '  - guide/items.md'];
  const result = verifyNonHeadingPlacement(indexLines, 'guide/items.md', 'Admin');
  assert.deepEqual(result, { kind: 'misplaced', foundContainer: 'Other' });
});

test('verifyNonHeadingPlacement, containerOwnerScan coverage [isolating, mutation-confirmed — same mutant as the fixture above]: a heading reset between two DIFFERENTLY-labeled containers correctly re-parents a post-reset child to the container that follows the reset, never the one that preceded it', () => {
  // If the ATX-heading reset of currentContainer were dropped (or misapplied), this child would be
  // read as still belonging to the PRE-reset "Admin" container and the file would wrongly verify ok.
  const indexLines = ['- Admin', '# Section', '- Other', '  - guide/items.md'];
  const result = verifyNonHeadingPlacement(indexLines, 'guide/items.md', 'Admin');
  assert.deepEqual(result, { kind: 'misplaced', foundContainer: 'Other' });
});

test('verifyNonHeadingPlacement, containerOwnerScan coverage [characterization, not paired with a dedicated discriminating mutant]: MULTIPLE children under one container — a MIDDLE child (neither first nor last) still resolves to the shared container', () => {
  // Honest limit, checked rather than assumed: verifyNonHeadingPlacement never surfaces a
  // container's own BODY index, only its LABEL — a mutant that mis-tracks `ownerOf`'s index (e.g.
  // "previous bullet" instead of "current container") but leaves `ownerLabelOf` correct is provably
  // unobservable through this public contract, so no mutation isolates THIS fixture specifically
  // from the plain single-child "ok" fixture above. Its value is coverage: it proves cardinality
  // (2+ children under one container) does not itself break the shared resolution.
  const indexLines = ['- Admin', '  - a.md', '  - guide/items.md', '  - c.md'];
  const result = verifyNonHeadingPlacement(indexLines, 'guide/items.md', 'Admin');
  assert.deepEqual(result, { kind: 'ok' });
});

// -------------------------------------------------------------------------------------------------
// selectedTarget, not a bare expected target (round-2 HIGH 6) — the Obsidian adapter's union scan
// over the qualified and legacy-bare spellings SELECTS one before calling this function; the
// verifier must check placement of whichever target the caller selected, never re-derive it.
// -------------------------------------------------------------------------------------------------

test('verifyNonHeadingPlacement, legacy-bare target selection [round-2 HIGH 6]: a legitimately-present LEGACY bare wikilink row verifies ok when selectedTarget is the legacy spelling the caller actually selected, never falsely inconsistent against the qualified spelling it did NOT select', () => {
  const indexLines = ['- Admin', '  - [[items]]'];
  const result = verifyNonHeadingPlacement(indexLines, 'items', 'Admin', { wikilink: true });
  assert.deepEqual(result, { kind: 'ok' });
});

test('verifyNonHeadingPlacement, wikilink ".md" fold: a markdown-link row is still recognized under wikilink mode once the fold removes the row\'s own terminal ".md" from the comparison, same as locateChapterLine\'s own fold', () => {
  const indexLines = ['- Admin', '  - [Items](guide/items.md)'];
  const result = verifyNonHeadingPlacement(indexLines, 'guide/items', 'Admin', { wikilink: true });
  assert.deepEqual(result, { kind: 'ok' });
});

// -------------------------------------------------------------------------------------------------
// Compatibility matrix (plan Tests item 2) — a finite, enumerated set of adapter-generated link
// equivalence classes, every one correctly placed, all converging on ok.
// -------------------------------------------------------------------------------------------------

test('verifyNonHeadingPlacement compatibility matrix: path mode, flat target -> ok', () => {
  const result = verifyNonHeadingPlacement(['- Admin', '  - [Items](guide/items.md)'], 'guide/items.md', 'Admin');
  assert.deepEqual(result, { kind: 'ok' });
});

test('verifyNonHeadingPlacement compatibility matrix: path mode, grouped (subfolder) target -> ok', () => {
  const result = verifyNonHeadingPlacement(
    ['- Admin', '  - [Items](guide/admin/items.md)'],
    'guide/admin/items.md',
    'Admin',
  );
  assert.deepEqual(result, { kind: 'ok' });
});

test('verifyNonHeadingPlacement compatibility matrix: wikilink mode, qualified target -> ok', () => {
  const result = verifyNonHeadingPlacement(['- Admin', '  - [[guide/items|Items]]'], 'guide/items', 'Admin', {
    wikilink: true,
  });
  assert.deepEqual(result, { kind: 'ok' });
});

test('verifyNonHeadingPlacement compatibility matrix: wikilink mode, legacy-bare target via a markdown-link row (".md" fold) -> ok', () => {
  // Distinct from round-2 HIGH 6's own fixture above (['- Admin', '  - [[items]]']): that one is
  // wikilink SYNTAX and never needs the fold at all. This row is markdown-link syntax carrying a
  // literal '.md' destination, recognized only because foldTargetForMatch strips one terminal
  // '.md' under wikilink mode (chapter-paths.mjs, foldTargetForMatch) — the SAME fold the
  // qualified-target test above (guide/items.md) already covers, exercised here against a BARE
  // target instead. Confirmed against a mutant that drops the fold (scratch chapter-paths.mjs
  // with foldTargetForMatch's `wikilink ? … : normalized` collapsed to `normalized`): this
  // fixture flips to `inconsistent` while the wikilink-syntax fixtures above are unaffected.
  const result = verifyNonHeadingPlacement(['- Admin', '  - [Items](items.md)'], 'items', 'Admin', {
    wikilink: true,
  });
  assert.deepEqual(result, { kind: 'ok' });
});

test('verifyNonHeadingPlacement compatibility matrix: flat bare-path child (no link syntax at all) -> ok', () => {
  const result = verifyNonHeadingPlacement(['- Admin', '  - items.md'], 'items.md', 'Admin');
  assert.deepEqual(result, { kind: 'ok' });
});

// -------------------------------------------------------------------------------------------------
// The fixed-probe design's soundness assumption (round-9 HIGH 1) was: "for any newline-free
// chapterLink, accept/decline is a function of (indexLines, groupTitle) alone."
//
// [1.11.0] That is NO LONGER TRUE unconditionally, and the fixture below hid it: `['- Admin']` is an
// EMPTY container, so no child can ever equal chapterLink and the membership guard cannot fire. The
// invariant now holds exactly while NO child bullet carries the link verbatim; when one does, the
// same file and groupTitle answer `present` instead of `inserted`. Both halves are pinned below.
//
// The fixed-probe design survives this, but for a different reason than the original one: rule 4
// accepts `inserted` and `present` alike, so the probe's verdict cannot change a placement outcome.
// The delegation depends on THAT now, not on invariance.
// -------------------------------------------------------------------------------------------------

test('wireNestedListChapter accept/decline invariance [pins the #330 fixed-probe assumption]: the SAME file/groupTitle accepts (kind + created) identically across a representative repertoire of newline-free chapterLink spellings', () => {
  const indexLines = ['- Admin'];
  const links = [
    '[Items](admin/items.md)',
    '[[admin/items|Items]]',
    '[[items]]',
    'admin/items.md',
    '[A very different label indeed](admin/other/path/items.md)',
  ];
  for (const link of links) {
    const result = wireNestedListChapter(indexLines, 'Admin', link);
    assert.equal(result.kind, 'inserted', `expected inserted for link ${JSON.stringify(link)}`);
    assert.equal(result.created, false, `expected created:false for link ${JSON.stringify(link)}`);
  }
});

test('wireNestedListChapter accept/decline invariance [1.11.0]: the invariance above holds only while no child carries the link — a colliding child flips the SAME file/groupTitle to `present`', () => {
  // The boundary the empty-container fixture above cannot reach. Same groupTitle, same file shape,
  // and the ONLY difference is whether a child bullet already carries the exact link — which is
  // precisely the input the round-9 invariant assumed could not matter.
  const link = '[Items](admin/items.md)';
  const withoutCollision = ['- Admin', '  - [Something else](admin/other.md)'];
  const withCollision = ['- Admin', `  - ${link}`];

  assert.equal(wireNestedListChapter(withoutCollision, 'Admin', link).kind, 'inserted');
  assert.deepEqual(wireNestedListChapter(withCollision, 'Admin', link), { kind: 'present', index: 1 });
});

// =================================================================================================
// [1.11.0 round 11] verifyNonHeadingPlacement TITLE-SHAPE x LINK-MODE x PLACEMENT matrix — exists so
// a false CLAIM about what a construct-bearing chapter-row TITLE does to the verifier's verdict
// fails a TEST, not merely a reworded needle in reference-assets.test.sh (which only pins that the
// adapter PROSE didn't drift — it structurally cannot prove the prose TRUE). Four claims about this
// exact surface were measured false across three review rounds, all three-reviewer-clean:
//   round 8:  promised `misplaced` unconditionally for a mis-nested row.
//   round 9:  "a non-plain title is never reported misplaced" — false: a backslash-escaped title
//             ("A\.B") is DECODED to plain ("A.B") by parseNestedLabel's mdlink branch and IS
//             reported misplaced in path mode (see the backslashEscape row, path/margin cell).
//   round 9:  "markup breaking the target halts as inconsistent" — an ADAPTER-level control-flow
//             claim (the verifier is never CALLED for that input), not a property of
//             verifyNonHeadingPlacement itself — out of this module's scope, not re-tested here.
//   round 10: "turns on what the title RENDERS as" — false twice, both isolated below: (a) a
//             wikilink ALIAS is never escape-decoded (parseNestedLabel's wikilink branch has no
//             decodeMarkdownEscapes call, unlike its mdlink branch) — the SAME backslash-escaped
//             title is misplaced in path mode but unverifiable in wikilink mode (backslashEscape
//             row, path vs wiki columns); (b) an HTML entity is decoded NOWHERE in this module, so
//             it behaves like a literal stray "&" in BOTH modes, never like the character it would
//             actually render as (htmlEntity row — unverifiable in both columns, never diverging the
//             way a real renderer would).
//
// Every cell is MEASURED against the real module (a probe script run against the shipped file, not
// hand-derived) — `target` is whichever selectedTarget makes locateChapterLine report exactly ONE
// match for that row's real content; four shapes do NOT resolve to the intended chapter destination
// at all (see each row's own comment) and that divergence is itself part of what this table pins —
// if any assertion below ever regresses to `{kind:'inconsistent'}`, that means the row's measured
// `target` stopped matching, which is exactly as informative a failure as a wrong `kind`.
// `wikilink:false` and `wikilink:true` content is genuinely parallel (the same `label` text, placed
// as a markdown-link label vs a wikilink alias) so a path/wiki verdict difference in the table is a
// real MODE difference (the round-10 axis), never an artifact of differently-worded fixtures.
// =================================================================================================

// One row per row-title shape. `label` is the literal text inside the outer link label (path mode:
// `[label](guide/items.md)`) or wikilink alias (wiki mode: `[[guide/items|label]]`) — the SAME
// `label` string drives both modes. `margin`/`nested` record the expected `.kind` at EACH placement,
// per mode: isPlainLabel is a CONTAINER-only gate (§5.1) — a nested/child row's own title shape
// almost never changes the verdict (only a MARGIN row's title is ever gated by it), so most rows'
// `nested` column reads `ok`/`ok` regardless of shape; where it does NOT (inlineCode), that is
// itself a distinct, separately-documented mechanism, not a copy-paste slip.
const TITLE_SHAPE_TABLE = [
  {
    shape: 'plain',
    label: 'Items',
    margin: { path: 'misplaced', wiki: 'misplaced' },
    nested: { path: 'ok', wiki: 'ok' },
    marginExtract: { path: { label: 'Items', plain: true }, wiki: { label: 'Items', plain: true } },
  },
  {
    shape: 'backslashEscape',
    label: 'A\\.B',
    // round 9 + round 10(a): path DECODES the escape ("A.B", plain) -> misplaced; wiki does NOT
    // decode the alias ("A\.B" survives with its literal backslash, non-plain) -> unverifiable.
    // Red-before-green: a scratch mutant that adds decodeMarkdownEscapes to parseNestedLabel's
    // wikilink branch flips ONLY the wiki/margin cell here (path/margin and every htmlEntity cell
    // stay unchanged under that same mutant) — see this teammate's report for the measured run.
    margin: { path: 'misplaced', wiki: 'unverifiable' },
    nested: { path: 'ok', wiki: 'ok' },
    marginExtract: { path: { label: 'A.B', plain: true }, wiki: { label: 'A\\.B', plain: false } },
  },
  {
    shape: 'htmlEntity',
    label: 'A&#46;B',
    // round 10(b): decoded in NEITHER mode -> unverifiable in both columns (a literal "&" is always
    // forbidden, isPlainLabel's own char class). Red-before-green: a scratch mutant that has
    // isPlainLabel decode a "&#NNN;" run before testing flips ONLY this row's margin cells (both
    // modes, since isPlainLabel is mode-agnostic) — see this teammate's report for the measured run.
    margin: { path: 'unverifiable', wiki: 'unverifiable' },
    nested: { path: 'ok', wiki: 'ok' },
    marginExtract: { path: { label: 'A&#46;B', plain: false }, wiki: { label: 'A&#46;B', plain: false } },
  },
  {
    shape: 'ampersand',
    label: 'A&B',
    margin: { path: 'unverifiable', wiki: 'unverifiable' },
    nested: { path: 'ok', wiki: 'ok' },
    marginExtract: { path: { label: 'A&B', plain: false }, wiki: { label: 'A&B', plain: false } },
  },
  {
    shape: 'emphasis',
    label: '*A*',
    margin: { path: 'unverifiable', wiki: 'unverifiable' },
    nested: { path: 'ok', wiki: 'ok' },
    marginExtract: { path: { label: '*A*', plain: false }, wiki: { label: '*A*', plain: false } },
  },
  {
    shape: 'inlineCode',
    label: '`A`',
    // A real (balanced) inline-code span anywhere in the file trips prepareIndexLines' own
    // whole-file inert-content refusal (stripInertContexts blanks the span; the resulting
    // SAN[i] !== fm[i] mismatch declines the FILE, chapter-paths.mjs step 6) BEFORE isPlainLabel is
    // ever consulted — unverifiable at BOTH placements, not just margin. Structurally distinct from
    // every other row here: it is the only one that is unverifiable when correctly nested.
    margin: { path: 'unverifiable', wiki: 'unverifiable' },
    nested: { path: 'unverifiable', wiki: 'unverifiable' },
    marginExtract: { path: { label: '`A`', plain: false }, wiki: { label: '`A`', plain: false } },
  },
  {
    // extractLineTargets has no nested-bracket support (its own documented limit,
    // findLinkOpeners/findMarkdownLinkGroups): the FIRST "]...(" pair found becomes THE link, so a
    // genuine nested link inside the outer label steals the destination — target is 'x' (the
    // NESTED link's own destination), never 'guide/items.md'/'guide/items'. Once that is accounted
    // for, the outer label itself falls to the RAW kind (it contains '[' and ']') — non-plain at
    // margin, irrelevant when nested.
    shape: 'nestedLink',
    label: 'A [nested](x) B',
    target: () => 'x',
    margin: { path: 'unverifiable', wiki: 'unverifiable' },
    nested: { path: 'ok', wiki: 'ok' },
    // Whole-content link/wikilink detection fails on the embedded brackets, so extractLabel falls
    // to the 'raw' branch and returns the ENTIRE row content verbatim (measured, not assumed).
    marginExtract: {
      path: { label: '[A [nested](x) B](guide/items.md)', plain: false },
      wiki: { label: '[[guide/items|A [nested](x) B]]', plain: false },
    },
  },
  {
    shape: 'nestedImage',
    label: 'A ![alt](y) B',
    target: () => 'y',
    margin: { path: 'unverifiable', wiki: 'unverifiable' },
    nested: { path: 'ok', wiki: 'ok' },
    marginExtract: {
      path: { label: '[A ![alt](y) B](guide/items.md)', plain: false },
      wiki: { label: '[[guide/items|A ![alt](y) B]]', plain: false },
    },
  },
  {
    // Reference-style "[text][ref]" is not "](" — findMarkdownLinkGroups never opens on it, and
    // WIKILINK_TARGET_RE cannot span the embedded ']'. Neither link syntax is recognized AT ALL, so
    // extractLineTargets' bare-YAML fallback takes the WHOLE row content (marker stripped) as the
    // target — `target` is a function of the actual row content, not a fixed destination string.
    shape: 'referenceLink',
    label: 'A [text][1] B',
    target: (content) => content,
    margin: { path: 'unverifiable', wiki: 'unverifiable' },
    nested: { path: 'ok', wiki: 'ok' },
    marginExtract: {
      path: { label: '[A [text][1] B](guide/items.md)', plain: false },
      wiki: { label: '[[guide/items|A [text][1] B]]', plain: false },
    },
  },
  {
    // Same "no link syntax recognized at all" mechanism as referenceLink above — an unescaped ']'
    // breaks both findMarkdownLinkGroups' and WIKILINK_TARGET_RE's closing-delimiter search.
    shape: 'unescapedBracket',
    label: 'A]B',
    target: (content) => content,
    margin: { path: 'unverifiable', wiki: 'unverifiable' },
    nested: { path: 'ok', wiki: 'ok' },
    marginExtract: {
      path: { label: '[A]B](guide/items.md)', plain: false },
      wiki: { label: '[[guide/items|A]B]]', plain: false },
    },
  },
];

// MEDIUM 5 (round-13): both fixture builders below used to be binary ternaries keyed on their
// input directly (`mode === 'wiki' ? … : …`, `placement === 'margin' ? … : …`) — silently correct
// today only because there are exactly two values of each; a third mode/placement added to a loop
// above would fall through to the ELSE branch and run against another cell's fixture, green and
// wrong. Keyed maps + an explicit unhandled-key throw close that failure mode structurally.
function wikilinkForMode(mode) {
  const WIKILINK_BY_MODE = { path: false, wiki: true };
  if (!(mode in WIKILINK_BY_MODE)) throw new Error(`TITLE_SHAPE_TABLE: unhandled mode '${mode}'`);
  return WIKILINK_BY_MODE[mode];
}

// BLOCKER 2(d) (round-13): the adapters' own tables caption their measurement precisely — "a row
// sitting AT THE LEFT MARGIN alongside a clean, correctly-formed 'Admin' container elsewhere in the
// same file" (obsidian-vault.md/static-md.md, "The plain-label predicate, named exactly.") — never
// reproduced before this round: the plain 'margin' fixture below is a LONE bullet, no other
// container. 'marginWithContainer' reproduces the adapters' literal fixture. Measured (this
// teammate, scratch-only, real module): every one of the 20 margin shape/mode cells reports the
// IDENTICAL verdict with or without the extra container — a separate correctly-formed container
// never rescues (or worsens) a badly-labelled margin row, consistent with the "cannot rescue" half
// of the whole-scan-abort claim pinned separately below — so expectedKindFor deliberately maps this
// placement back onto the SAME `row.margin` expectations rather than a duplicated column.
function buildTitleShapeFixture(placement, content, mode) {
  const BUILDERS = {
    margin: () => [`- ${content}`],
    marginWithContainer: () => {
      const cleanChild = mode === 'wiki' ? '  - [[guide/other|Other]]' : '  - [Other](guide/other.md)';
      return ['- Admin', cleanChild, `- ${content}`];
    },
    nested: () => ['- Admin', `  - ${content}`],
  };
  if (!(placement in BUILDERS)) throw new Error(`TITLE_SHAPE_TABLE: unhandled placement '${placement}'`);
  return BUILDERS[placement]();
}

function expectedKindFor(row, placement, mode) {
  const KIND_KEY_BY_PLACEMENT = { margin: 'margin', marginWithContainer: 'margin', nested: 'nested' };
  if (!(placement in KIND_KEY_BY_PLACEMENT)) throw new Error(`TITLE_SHAPE_TABLE: unhandled placement '${placement}'`);
  return row[KIND_KEY_BY_PLACEMENT[placement]][mode];
}

for (const row of TITLE_SHAPE_TABLE) {
  for (const mode of ['path', 'wiki']) {
    const wikilink = wikilinkForMode(mode);
    const content = wikilink ? `[[guide/items|${row.label}]]` : `[${row.label}](guide/items.md)`;
    const target = row.target ? row.target(content) : wikilink ? 'guide/items' : 'guide/items.md';
    for (const placement of ['margin', 'marginWithContainer', 'nested']) {
      const expectedKind = expectedKindFor(row, placement, mode);
      test(`verifyNonHeadingPlacement title-shape matrix [round-11]: ${row.shape} / ${mode} / ${placement} -> ${expectedKind}`, () => {
        const indexLines = buildTitleShapeFixture(placement, content, mode);
        const result = verifyNonHeadingPlacement(indexLines, target, 'Admin', { wikilink });
        assert.equal(
          result.kind,
          expectedKind,
          `${row.shape}/${mode}/${placement}: expected ${expectedKind}, got ${JSON.stringify(result)} (indexLines=${JSON.stringify(indexLines)})`,
        );
        if (expectedKind === 'misplaced') assert.equal(result.foundContainer, null);

        // HIGH 3 (round-13): the adapters' own tables also publish `extractLabel` and
        // `isPlainLabel` per row, not just the final verdict — changing the extracted spelling
        // while keeping it non-plain would leave `result.kind` green and falsify those published
        // columns silently. Margin placement only: that table is explicitly captioned "Measured
        // for a row sitting AT THE LEFT MARGIN…", and isPlainLabel is a CONTAINER-only (indent-0)
        // gate a nested child's own label never reaches (see the whole-scan-abort tests below).
        // Asserted against MEASURED literals (this teammate, scratch-only, against the real
        // module), never by calling extractLabel/isPlainLabel again here — re-deriving the
        // expectation from the same functions under test would make this a tautology no mutant to
        // either function could ever fail.
        if (placement === 'margin') {
          const expectedExtract = row.marginExtract[mode];
          const actualExtract = extractLabel(content);
          assert.equal(
            actualExtract,
            expectedExtract.label,
            `${row.shape}/${mode}/margin: extractLabel drifted from the published table (got ${JSON.stringify(actualExtract)})`,
          );
          assert.equal(
            isPlainLabel(actualExtract),
            expectedExtract.plain,
            `${row.shape}/${mode}/margin: isPlainLabel drifted from the published table (extractLabel=${JSON.stringify(actualExtract)})`,
          );
        }
      });
    }
  }
}

// =================================================================================================
// [1.11.0 round 11-b] adapter composition flow — the duplicate-insert warning (static-md.md /
// obsidian-vault.md step-4 disclosure paragraph), UPDATED for the [1.11.0] membership guard added
// to wireNestedListChapter's single-container branch (chapter-paths.mjs's SINGLE branch, "Refuse to
// write a row this container already carries"). The title-shape matrix above deliberately drives
// verifyNonHeadingPlacement with each shape's own MEASURED (sometimes hijacked) target, so it can
// isolate placement-verification in isolation — that is NOT what the real adapter does. The real
// adapter always searches for the chapter's REAL expected target. For the four shapes that corrupt
// destination extraction (a nested link, a nested image, a reference link, or an unescaped ']' in
// the title), that search finds NOTHING — even when the row already sits correctly nested under
// the container — so step 0 reports the chapter absent and the adapter falls through to
// wireNestedListChapter. What happens next now depends on whether the chapterLink the adapter asks
// the writer to insert is byte-identical to the row already there:
// - ARM 1 below drives the writer with a harmless, differently-worded link (isolating the general
//   duplicate-insert risk in the abstract) — the guard cannot recognize two different-content rows
//   as "the same", so it STILL inserts a second, duplicate row; the next run finds the fresh row
//   and reports `ok`, while the original malformed row lingers as a silent, undetected duplicate
//   (measured here against the real functions, and deliberately NOT cited to adapter prose — a
//   comment quoting a document is a citation nothing re-checks when that document is rewritten).
//   This arm is unaffected by the guard.
// - ARM 2 below drives the writer the REALISTIC way (obsidian-vault.md: "display text is always the
//   manifest entry's `title`") — the SAME manifest title generates the inserted link on every
//   publish, so the "new" link is byte-identical to the malformed row already present. The guard
//   now recognizes that and refuses (`{kind:'present'}`) instead of inserting — the unbounded row
//   growth this section originally existed to document no longer happens for this arm. See the
//   convergence tests following this loop for the repeated-publish property the fix guarantees.
// This section drives the EXACT sequence — step 0, branch, write, effect, re-run — with the real
// functions and the real (non-hijacked) target; the title-shape matrix above cannot cover it by
// construction, because it passes the hijacked target on purpose to exercise the isPlainLabel path
// instead.
// =================================================================================================

// MEDIUM 5 (round-13): derive shared shape labels from TITLE_SHAPE_TABLE rather than re-typing them
// here and in CONTRAST_SHAPES below — a hand-duplicated label is exactly the drift risk that let
// the two groups silently test different inputs for the "same" shape name.
const LABEL_BY_SHAPE = Object.fromEntries(TITLE_SHAPE_TABLE.map((row) => [row.shape, row.label]));

const DUPLICATE_INSERT_SHAPE_NAMES = ['nestedLink', 'nestedImage', 'referenceLink', 'unescapedBracket'];

// Row counter for every duplication/convergence assertion below. Independent of the module's own
// parse (extractLineTargets/parseNestedLabel) on purpose: a counter built from the functions under
// test would only re-derive what they already believe, and would agree with a broken one.
//
// Exact-content, never a substring: a substring check matches a malformed row that merely CONTAINS
// the destination text but never parses as it — precisely the laxness this release rejects for the
// guard itself, and precisely the inputs this suite exists to discriminate.
const countRowsCarrying = (lines, chapterLink) =>
  lines.filter((line) => {
    const bm = line.match(/^ *[-*+] (.*)$/);
    return bm !== null && bm[1] === chapterLink;
  }).length;

for (const mode of ['path', 'wiki']) {
  const wikilink = wikilinkForMode(mode);
  const realTarget = wikilink ? 'guide/items' : 'guide/items.md';
  for (const shape of DUPLICATE_INSERT_SHAPE_NAMES) {
    const label = LABEL_BY_SHAPE[shape];
    const malformedRow = wikilink ? `[[guide/items|${label}]]` : `[${label}](guide/items.md)`;

    // ARM 1 — harmless writer link. A stale unrecognizable row plus a CLEAN current manifest title:
    // the writer's own insert resolves, so exactly one lingering duplicate forms and the next run
    // reports `ok` on the clean row. True only because `realLink`'s own display text ('Items') does
    // not itself corrupt its target extraction — that is the whole difference from ARM 2 below.
    //
    // Asserted against the CODE and the measurement below, deliberately not against a quoted
    // sentence from the adapter docs.
    test(`adapter composition flow [round-11-b, mutation-confirmed]: ${shape} / ${mode}, already correctly nested, leaves exactly ONE lingering duplicate then converges (writer link harmless)`, () => {
      const realLink = wikilink ? '[[guide/items|Items]]' : '[Items](guide/items.md)';
      const indexLines = ['- Admin', `  - ${malformedRow}`];

      // step 0: the adapter searches for the REAL target, never the shape's own hijacked one.
      const step0 = locateChapterLine(indexLines, realTarget, { wikilink });
      assert.equal(step0.matches.length, 0, "the malformed row's own corrupted destination must never satisfy the real target");

      // branch: line-absent -> the adapter calls the writer. Its 1.11.0 membership guard does not
      // fire here: the guard compares a child's content to `realLink` VERBATIM, and the stale row
      // carries a different label, so this insert is correct rather than a missed duplicate.
      const write = wireNestedListChapter(indexLines, 'Admin', realLink);
      assert.equal(write.kind, 'inserted');
      assert.equal(write.created, false, 'the "Admin" container already exists — this is a CHILD insert, not a create');

      // effect: row count grew by exactly one, and the OLD malformed row survives verbatim — this
      // is a DUPLICATE, never a replacement. Mutation-confirmed (scratch-only, never run against
      // the shared tree): a mutant that gives wireNestedListChapter's single-container branch a
      // destination-substring membership check flips every insert assertion in BOTH this arm and
      // the target-breaking arm below — measured: the malformed row already contains the REAL
      // destination text verbatim (it is simply never PARSED as the target), so a substring check
      // against `realLink`'s destination ('guide/items.md'/'guide/items') matches here too, even
      // though `realLink`'s own display text ('Items') shares nothing with the malformed row's
      // label. `write.newLines` comes back byte-identical to `indexLines` (grew: 0, not 1) under
      // that mutant, the re-run below finds 0 matches instead of 1, and the verdict comes back
      // `inconsistent` instead of `ok`. The five contrast fixtures below never call
      // wireNestedListChapter at all, so that same mutant leaves every one of them unchanged.
      assert.equal(
        write.newLines.length,
        indexLines.length + 1,
        `expected exactly one row inserted, got ${JSON.stringify(write.newLines)}`,
      );
      assert.ok(write.newLines.includes(`  - ${malformedRow}`), 'the original malformed row must survive untouched, not be replaced');

      // re-run: the freshly-inserted row IS found this time and reports ok — completing SILENTLY
      // on a now-duplicated index; nothing here ever flags the earlier, still-present row.
      const rerun = locateChapterLine(write.newLines, realTarget, { wikilink });
      assert.equal(rerun.matches.length, 1);
      const verdict = verifyNonHeadingPlacement(write.newLines, realTarget, 'Admin', { wikilink });
      assert.deepEqual(verdict, { kind: 'ok' });
    });

    // ARM 2 — target-breaking writer link (BLOCKER 1, round-13; CLOSED by the [1.11.0] membership
    // guard in wireNestedListChapter's SINGLE branch): the adapters build the inserted child's link
    // from the chapter's manifest `title` (obsidian-vault.md: "display text is always the manifest
    // entry's `title`, never a slug or a hand-typed label" — that sentence lives in obsidian-vault.md
    // only, 1x, and has never appeared in static-md.md) — never from a constant safe string as
    // ARM 1 above drives it. When that title carries the SAME corrupting shape that broke the
    // original row — the realistic case, since it is the identical manifest field driving both the
    // original row and any fresh insert for this chapter — the freshly-inserted row's own
    // destination extraction WOULD be hijacked too, exactly like the original. Before the guard,
    // step 0 never found the fresh row either, so a driver following the documented recipe took the
    // line-absent branch again and again, and row growth repeated without bound.
    //
    // The guard closes exactly this case: because the SAME manifest title drives the insert on
    // every publish, the "new" link the writer is asked to write is byte-identical to the malformed
    // row already sitting there — so the guard's own verbatim content check fires on the very FIRST
    // call, before any insert happens, and the writer reports `{kind:'present'}` instead. Nothing is
    // ever written; the file never changes. This is deliberately NOT the same as `ok`: step 0 still
    // cannot resolve the row (its destination is still corrupted), so a driver would still see this
    // chapter as unwired and must halt for manual repair — the guard trades unbounded silent growth
    // for a stable, detectable non-convergence, never a false completion. The harmless-arm test
    // above is unaffected by design: it drives the writer with a DIFFERENT, constant safe link, so
    // the two rows' content never matches and the guard never fires. See the convergence tests
    // following this loop for the repeated-publish property this fix actually guarantees.
    test(`adapter composition flow [round-11-b, mutation-confirmed]: ${shape} / ${mode}, already correctly nested, writer link target-breaking now converges (present, byte-identical, never duplicates)`, () => {
      const breakingLink = malformedRow; // same manifest title drives both the row and any insert
      const indexLines = ['- Admin', `  - ${malformedRow}`];

      const step0 = locateChapterLine(indexLines, realTarget, { wikilink });
      assert.equal(step0.matches.length, 0, "the malformed row's own corrupted destination must never satisfy the real target");

      // The guard fires on the FIRST call: the existing child's content already equals chapterLink
      // verbatim (the same manifest title drives both), so the writer refuses instead of inserting
      // a duplicate that step 0 could never itself have detected.
      const write1 = wireNestedListChapter(indexLines, 'Admin', breakingLink);
      assert.equal(write1.kind, 'present', 'the guard must recognize the existing row as this exact chapterLink and refuse to insert');
      assert.equal(write1.index, 1, 'the existing malformed child sits at index 1 of indexLines');
      assert.ok(
        !('newLines' in write1),
        'a present outcome carries no newLines: this is how the caller tells present apart from inserted without inspecting content',
      );

      // Convergence: nothing was ever persisted (there is nothing TO persist), so a driver repeating
      // the identical publish keeps re-presenting the SAME unchanged indexLines — and gets the SAME
      // present verdict every time, never a second or third insert.
      for (let i = 0; i < 4; i += 1) {
        const repeat = wireNestedListChapter(indexLines, 'Admin', breakingLink);
        assert.deepEqual(repeat, { kind: 'present', index: 1 }, `run ${i + 2}: expected the SAME present verdict, got ${JSON.stringify(repeat)}`);
      }

      // The guard does not repair the underlying blind spot — it only stops the write loop from
      // making it WORSE. Step 0 still cannot resolve this row on its own terms, so the caller must
      // still surface a manual halt (present + step-0-absent), never silently treat this as done.
      const step0Still = locateChapterLine(indexLines, realTarget, { wikilink });
      assert.equal(
        step0Still.matches.length,
        0,
        'the underlying target-parse blind spot is unchanged — only the runaway duplication is fixed',
      );
    });
  }
}

// =================================================================================================
// [1.11.0] CONVERGENCE — the test whose absence let the unbounded-duplicate-insert bug ship. ARM 2
// above pins the fix at a SINGLE call (write1.kind === 'present'); it does not by itself prove the
// bug is CLOSED across repeated publishes — nothing before this test drove the actual adapter loop
// (step 0 -> absent -> wireNestedListChapter) more than once against an unchanged manifest with a
// target-breaking title. This section drives exactly that, starting from an EMPTY container (no
// existing chapter row at all — the realistic first-publish state), for 5 consecutive publishes,
// and watches the row count directly rather than trusting a single before/after snapshot. Before the
// guard this would have failed outright: run 1 inserts (row count 1, as expected), but run 2 would
// insert AGAIN (row count 2, the bug), and every run after that would grow the count further,
// unbounded. After the guard: run 1 inserts and every run after it reports `present` against the
// SAME unchanged indexLines (nothing is ever persisted once the guard starts firing), so the row
// count is exactly 1 from run 1 onward and never grows again.
// =================================================================================================

for (const mode of ['path', 'wiki']) {
  const wikilink = wikilinkForMode(mode);
  const realTarget = wikilink ? 'guide/items' : 'guide/items.md';
  for (const shape of DUPLICATE_INSERT_SHAPE_NAMES) {
    const label = LABEL_BY_SHAPE[shape];
    const chapterLink = wikilink ? `[[guide/items|${label}]]` : `[${label}](guide/items.md)`;

    test(`adapter composition flow convergence [round-11-b follow-up]: ${shape} / ${mode}, 5 publishes of an unchanged manifest converge to exactly ONE row from the first run onward`, () => {
      let current = ['- Admin']; // empty container, no existing chapter row yet — the first publish

      assert.equal(countRowsCarrying(current, chapterLink), 0, 'sanity: no row exists before the first publish');

      for (let run = 1; run <= 5; run += 1) {
        // step 0: the adapter's own idempotency check over the REAL target. Always absent here —
        // this chapter title's own corrupted destination extraction never resolves to realTarget,
        // neither on the original row nor on any fresh row this shape would ever produce.
        const step0 = locateChapterLine(current, realTarget, { wikilink });
        assert.equal(
          step0.matches.length,
          0,
          `run ${run}: step 0 must report absent — that is exactly the blind spot the guard covers, not repairs`,
        );

        const write = wireNestedListChapter(current, 'Admin', chapterLink);
        // Only an 'inserted' outcome is ever persisted; a 'present' outcome means the caller writes
        // nothing back, so `current` stays exactly what it was — itself part of the invariant this
        // test pins (no silent, unrecorded progress).
        if (write.kind === 'inserted') current = write.newLines;

        assert.equal(
          countRowsCarrying(current, chapterLink),
          1,
          `run ${run}: expected exactly one row for this chapter, got ${JSON.stringify(current)}`,
        );
      }
    });
  }
}

// -------------------------------------------------------------------------------------------------
// Contrast: a title that merely RENDERS non-plain (or decodes to plain in path mode) WITHOUT
// corrupting its own row's target extraction. Precision note (measured directly here, not taken
// from the adapter prose as given): these three shapes do NOT share one verdict — path-mode
// backslashEscape decodes to a plain label and is `misplaced` (round 9's own finding, already
// pinned by the title-shape matrix above); ampersand/emphasis stay non-plain in both modes and are
// `unverifiable`. What they DO share, and what this group exists to isolate, is the one property
// that actually rules out the duplicate-insert bug above: step 0 finds every one of them PRESENT
// (never absent), because none of their own link destinations were ever corrupted — so the
// writer's blind-insert branch is never reached at all, regardless of which of the two verdicts
// placement-verification lands on afterward.
// -------------------------------------------------------------------------------------------------

const CONTRAST_SHAPES = [
  { name: 'backslashEscape', mode: 'path', expected: { kind: 'misplaced', foundContainer: null } },
  { name: 'ampersand', mode: 'path', expected: { kind: 'unverifiable' } },
  { name: 'ampersand', mode: 'wiki', expected: { kind: 'unverifiable' } },
  { name: 'emphasis', mode: 'path', expected: { kind: 'unverifiable' } },
  { name: 'emphasis', mode: 'wiki', expected: { kind: 'unverifiable' } },
];

for (const { name, mode, expected } of CONTRAST_SHAPES) {
  const wikilink = wikilinkForMode(mode);
  const realTarget = wikilink ? 'guide/items' : 'guide/items.md';
  const label = LABEL_BY_SHAPE[name];
  const row = wikilink ? `[[guide/items|${label}]]` : `[${label}](guide/items.md)`;
  test(`adapter composition flow [round-11-b]: contrast, ${name} / ${mode}, target still resolves -> found, never duplicated`, () => {
    const indexLines = [`- ${row}`]; // left margin, matching the adapters' own contrast framing
    const step0 = locateChapterLine(indexLines, realTarget, { wikilink });
    assert.equal(
      step0.matches.length,
      1,
      "the row's own target resolves correctly, so it is FOUND -- the writer's blind-insert branch is never reachable from here, regardless of placement verdict",
    );
    const verdict = verifyNonHeadingPlacement(indexLines, realTarget, 'Admin', { wikilink });
    assert.deepEqual(verdict, expected);
  });
}

// =================================================================================================
// [1.11.0] membership guard — contrast cases that must NOT regress. The ARM 2 / CONVERGENCE tests
// above pin what the guard DOES catch (an exact-content resubmission); the cases below pin the
// guard's SCOPE — every shape of call it must leave alone, asserted explicitly rather than assumed.
// =================================================================================================

for (const mode of ['path', 'wiki']) {
  const wikilink = wikilinkForMode(mode);
  const realTarget = wikilink ? 'guide/items' : 'guide/items.md';
  const resolvableLink = wikilink ? '[[guide/items|Items]]' : '[Items](guide/items.md)';
  test(`membership guard contrast [round-11-b]: ${mode}, a chapter link that already resolves is found by step 0 alone -- the writer (and its guard) is never reached`, () => {
    const indexLines = ['- Admin', `  - ${resolvableLink}`];
    const step0 = locateChapterLine(indexLines, realTarget, { wikilink });
    assert.equal(step0.present, true, 'a correctly-resolving row must be found by step 0 alone');
    assert.equal(step0.matches.length, 1);
    // Per the documented recipe (obsidian-vault.md/static-md.md), step-0-present short-circuits
    // straight to placement verification; wireNestedListChapter is never invoked on this path at
    // all, so there is nothing here for the guard to either catch or miss.
    const verdict = verifyNonHeadingPlacement(indexLines, realTarget, 'Admin', { wikilink });
    assert.deepEqual(verdict, { kind: 'ok' }, 'placement verification must confirm ok without ever inserting');
  });
}

test('membership guard contrast [round-11-b]: a broken existing row plus a CLEAN manifest title still gets its own resolvable row inserted -- the guard must not suppress an insert when the two rows\' content differs', () => {
  const brokenRow = '[A [nested](x) B](guide/items.md)'; // nestedLink shape -- corrupts its OWN target
  const cleanLink = '[Items](guide/items.md)'; // a differently-worded, resolvable insert
  const indexLines = ['- Admin', `  - ${brokenRow}`];

  const write = wireNestedListChapter(indexLines, 'Admin', cleanLink);
  assert.equal(
    write.kind,
    'inserted',
    'the guard only refuses an EXACT verbatim resubmission -- a differently-worded clean link must still be inserted',
  );
  assert.equal(write.newLines.length, indexLines.length + 1);
  assert.ok(write.newLines.includes(`  - ${brokenRow}`), 'the broken row must survive untouched, not be replaced');
  assert.ok(write.newLines.includes(`  - ${cleanLink}`), 'the clean row must be freshly inserted');

  const rerun = locateChapterLine(write.newLines, 'guide/items.md', { wikilink: false });
  assert.equal(rerun.matches.length, 1, 'only the clean row resolves; the broken row stays invisible to step 0, exactly as documented');
  const verdict = verifyNonHeadingPlacement(write.newLines, 'guide/items.md', 'Admin', { wikilink: false });
  assert.deepEqual(verdict, { kind: 'ok' });
});

test('membership guard contrast [round-11-b]: the ZERO-container CREATE branch is unaffected, even when the exact chapterLink text already exists under an UNRELATED container', () => {
  const chapterLink = '[Items](guide/items.md)';
  // The exact link text already exists, but under "Other", not "Admin" -- no "Admin" container
  // exists at all, so this must fall straight through to the ZERO create path. The guard lives
  // entirely inside the containers.length === 1 branch (see wireNestedListChapter's SINGLE
  // branch) and is structurally unreachable here.
  const indexLines = ['- Other', `  - ${chapterLink}`];
  const write = wireNestedListChapter(indexLines, 'Admin', chapterLink);
  assert.equal(write.kind, 'inserted');
  assert.equal(write.created, true, 'a fresh "Admin" container must be created, not confused with the unrelated "Other" one');
  assert.ok(write.newLines.includes('- Admin'), 'the new container line must be spliced in');
  assert.equal(
    countRowsCarrying(write.newLines, chapterLink),
    2,
    'two rows now carry this exact content -- one under Other (untouched), one freshly created under Admin',
  );
});

test('membership guard contrast [round-11-b]: the {kind:"multiple"} halt is unaffected -- two "Admin" containers each already carrying this exact chapterLink still halt, never a false present', () => {
  const chapterLink = '[Items](guide/items.md)';
  const indexLines = ['- Admin', `  - ${chapterLink}`, '- Admin', `  - ${chapterLink}`];
  const write = wireNestedListChapter(indexLines, 'Admin', chapterLink);
  assert.equal(
    write.kind,
    'multiple',
    'container ambiguity must halt BEFORE the single-container membership guard is ever consulted',
  );
  assert.equal(write.matches.length, 2);
});

test('membership guard contrast [round-11-b]: the present path is CRLF-faithful -- a CRLF file with an exact verbatim child still refuses via present, not a false insert', () => {
  const chapterLink = '[Items](guide/items.md)';
  // Mirrors the caller's own split('\n') over a CRLF file on disk (chapter-paths.mjs's own contract:
  // "a CRLF file leaves a trailing '\r' per elem").
  const raw = ['- Admin', `  - ${chapterLink}`].join('\r\n') + '\r\n';
  const indexLines = raw.split('\n');
  const write = wireNestedListChapter(indexLines, 'Admin', chapterLink);
  assert.equal(write.kind, 'present');
  assert.equal(write.index, 1);
  assert.ok(
    !('newLines' in write),
    'present carries no newLines -- the caller can tell present apart from inserted without inspecting content',
  );
});

test('membership guard contrast [round-11-b]: the present path is terminal-newline-faithful -- a file with NO trailing newline still refuses via present', () => {
  const chapterLink = '[Items](guide/items.md)';
  const raw = ['- Admin', `  - ${chapterLink}`].join('\n'); // no trailing newline
  const indexLines = raw.split('\n');
  const write = wireNestedListChapter(indexLines, 'Admin', chapterLink);
  assert.equal(write.kind, 'present');
  assert.equal(write.index, 1);
  assert.ok(!('newLines' in write), 'present carries no newLines regardless of the source file\'s terminal-newline shape');
});

// =================================================================================================
// [1.11.0] The fixed-probe shape gate accepts `present` as well as `inserted`, and until this test
// nothing executed that. Adding a fourth writer outcome silently widened the gate: rule 4 was
// written as a negative list (decline `not-a-list`/`multiple`), so `present` joined the accepted set
// with no code change and no test change — while the class sentence pinned verbatim in FOUR files
// still said `inserted` alone. Four green pins, one false claim, exactly the shape this release
// keeps finding.
//
// The fixture is the one the reviewer constructed: an index that already carries the probe row, so
// the writer's membership guard fires on the probe itself. Accepting it is CORRECT — `present` means
// the writer recognized the shape and resolved exactly one container, which is all rule 4 asks, and
// the probe's emission is discarded either way — but it must be asserted, not inherited.
// =================================================================================================

test('verifyNonHeadingPlacement [1.11.0]: the fixed-probe shape gate accepts a `present` verdict, not only `inserted`', () => {
  const probeLink = '[probe](__verify-non-heading-placement-probe__.md)';
  const indexLines = ['- Admin', `  - ${probeLink}`, '  - [Items](guide/items.md)'];

  // Precondition: the probe row really does make the writer answer `present` for this index.
  // Without this the test could pass while never exercising the widened branch at all.
  const probeVerdict = wireNestedListChapter(indexLines, 'Admin', probeLink);
  assert.equal(probeVerdict.kind, 'present', 'fixture must drive the writer to `present`, not `inserted`');
  assert.equal(probeVerdict.index, 1, 'the probe row is the one recognized');

  // The chapter itself is correctly placed under the container named by group_title, so the verdict
  // must be `ok` — the gate must not answer `unverifiable` merely because the probe was recognized.
  const verdict = verifyNonHeadingPlacement(indexLines, 'guide/items.md', 'Admin');
  assert.deepEqual(verdict, { kind: 'ok' });
});

test('verifyNonHeadingPlacement [1.11.0]: a `present` probe verdict still reports `misplaced` when the chapter sits elsewhere', () => {
  // Same widened gate, opposite placement: passing rule 4 must not short-circuit rule 5's comparison.
  const probeLink = '[probe](__verify-non-heading-placement-probe__.md)';
  const indexLines = ['- Admin', `  - ${probeLink}`, '- Other', '  - [Items](guide/items.md)'];

  assert.equal(wireNestedListChapter(indexLines, 'Admin', probeLink).kind, 'present');
  assert.deepEqual(verifyNonHeadingPlacement(indexLines, 'guide/items.md', 'Admin'), {
    kind: 'misplaced',
    foundContainer: 'Other',
  });
});

// =================================================================================================
// [1.11.0] The `present` outcome's TRIGGER CONDITION differs by link mode, and obsidian-vault.md now
// states that difference as fact. Nothing executed it until this table, which is the whole point:
// the prose in this release has been measured false four times, every time by a probe that varied
// the obviously-relevant dimension while an unlisted one decided the answer. This one nearly shipped
// the same way. The lead measured six bracket-mangling titles in wikilinks mode, saw all six
// recognized by step 0, and briefed a teammate that no wikilinks route to `present` had been found;
// the teammate refuted it with `A]B`, and the lead reproduced the refutation independently.
//
// The dimension the lead's six shapes held fixed, without listing it: WHERE the `]` sits relative to
// the wikilink terminator. WIKILINK_TARGET_RE (`:482`) is /\[\[([^\]|#^]+)[^\]]*\]\]/ — after the
// target it accepts only non-`]` characters up to a closing `]]`. So a title containing `]]`, or a
// `]` adjacent to the closing pair, lets the match terminate early and step 0 still finds the row;
// an isolated `]` cannot be consumed and the whole match fails. Path mode has no such escape: the
// destination search stops at the FIRST `]`, so any `]` at all breaks it. Hence titles that break
// path mode but not wikilinks mode, which is exactly the asymmetry the adapter documents.
//
// Expectations below are MEASURED VALUES, hardcoded. They are deliberately not derived at test time
// from the same regex the code uses — a table that recomputed them would agree with any regex,
// including a broken one, and would have agreed with the lead's wrong claim too.
//
// Red-before-green, measured against two independent baselines (scratch copies, never the shared
// tree). Against SHIPPED 1.10.0: 13 of the 22 cells go red — every `present` expectation reports
// `inserted` instead, which is the unbounded-growth defect itself, while all 9 `step0` cells pass
// unchanged (correct: the guard does not touch the path step 0 already recognizes). Against a mutant
// that loosens WIKILINK_TARGET_RE's post-target run to accept `]` (`[^\]]*` -> `[\s\S]*?`): exactly
// the 5 wiki cells asserting `present` flip to step0-recognized, and NOTHING else moves — so those
// cells pin the mode asymmetry specifically, not merely the guard's existence.
// =================================================================================================

// Keyed rows rather than positional triples, matching CONTRAST_SHAPES above: `why` is data, so a red
// cell explains in its own name why that title was expected to break, without opening the table.
const PRESENT_TRIGGER_TABLE = [
  { title: 'A]B', path: 'present', wiki: 'present', why: "isolated ']' breaks both" },
  { title: 'Items]Beta', path: 'present', wiki: 'present', why: "isolated ']' breaks both" },
  { title: 'X]Y]Z', path: 'present', wiki: 'present', why: "two isolated ']' break both" },
  { title: '] Items', path: 'present', wiki: 'present', why: "leading ']' breaks both" },
  { title: 'Items] [Beta]', path: 'present', wiki: 'present', why: "first ']' is isolated" },
  { title: 'Items [beta]', path: 'present', wiki: 'step0', why: "trailing ']' is adjacent to the wiki terminator" },
  { title: 'Items ]] beta', path: 'present', wiki: 'step0', why: "contains ']]', terminating the wikilink early" },
  { title: 'Items ]]]] x', path: 'present', wiki: 'step0', why: "longer ']' run, still terminates early" },
  { title: 'Items | beta', path: 'step0', wiki: 'step0', why: "no ']' at all" },
  { title: 'Items [[beta', path: 'step0', wiki: 'step0', why: "no ']' at all" },
  { title: 'Items', path: 'step0', wiki: 'step0', why: 'plain control' },
];

for (const row of PRESENT_TRIGGER_TABLE) {
  for (const mode of ['path', 'wiki']) {
    const { title, why } = row;
    const expected = row[mode];
    test(`present trigger condition [1.11.0]: ${JSON.stringify(title)} / ${mode} -> ${expected} (${why})`, () => {
      const wikilink = mode === 'wiki';
      const target = wikilink ? 'admin/items' : 'admin/items.md';
      const chapterLink = wikilink ? `[[admin/items|${title}]]` : `[${title}](admin/items.md)`;
      const seed = wikilink
        ? ['# Summary', '', '- Admin', '  - [[admin/overview|Overview]]', '']
        : ['# Summary', '', '- Admin', '  - [Overview](admin/overview.md)', ''];

      // Run 1 always inserts: the row is genuinely absent from a fresh index in both modes.
      const first = wireNestedListChapter(seed, 'Admin', chapterLink);
      assert.equal(first.kind, 'inserted', 'the first publish always writes the row');

      // Run 2 is the discriminator: either step 0 recognizes what run 1 wrote (and the adapter
      // never calls the writer), or it does not and the writer's own guard must catch it.
      const after = first.newLines;
      const step0 = locateChapterLine(after, target, { wikilink });
      if (expected === 'step0') {
        assert.equal(step0.present, true, 'step 0 must recognize the row this title produces');
      } else {
        assert.equal(step0.present, false, 'this title must defeat step 0 — otherwise the guard is untested here');
        const second = wireNestedListChapter(after, 'Admin', chapterLink);
        assert.equal(second.kind, 'present', 'the writer must refuse to write a second copy');
      }

      // Run 1 wrote exactly one row. This is NOT the convergence property — that is proven over
      // five publishes by the CONVERGENCE section above, and claiming it here would overstate what
      // a single write asserts.
      assert.equal(
        countRowsCarrying(after, chapterLink),
        1,
        'the first publish writes exactly one row carrying this link',
      );
    });
  }
}

// =================================================================================================
// [1.11.0 round 13] BLOCKER 2(c) — the whole-scan-abort claim (obsidian-vault.md / static-md.md,
// "The plain-label predicate, named exactly."): the container-owner scan applies `isPlainLabel` to
// EVERY indent-0 bullet in the file, not only the row under test, so a single non-plain indent-0
// label ANYWHERE in the file declines the WHOLE scan — an otherwise-clean 'Admin' container
// elsewhere cannot rescue a badly-labelled row sitting at the left margin. Untested before this
// round: every `not-a-list` fixture elsewhere in this file is a single-bullet file, so nothing
// proved the claim is about indent-0 specifically rather than about labels in general. Measured
// directly here (this teammate, real module) and mutation-confirmed: a mutant that narrows the
// indent-0 plain-label gate to only the candidate matching `wanted` (i.e. checks a row's own
// labelling only when it could BE the target container, never every other indent-0 bullet) flips
// ONLY the first test below (from `unverifiable` to `ok`) and leaves the contrast/complement tests
// unchanged — see this teammate's report for the measured run.
// =================================================================================================

test('verifyNonHeadingPlacement whole-scan-abort [round-13, BLOCKER 2c]: a non-plain indent-0 label ANYWHERE declines the whole scan, even alongside a clean container', () => {
  // A clean, correctly-nested 'Admin' container with its own chapter row, PLUS an unrelated
  // non-plain indent-0 bullet ('*Other*') sitting elsewhere in the same file.
  const indexLines = ['- Admin', '  - [Items](<guide/items.md>)', '- *Other*'];
  const verdict = verifyNonHeadingPlacement(indexLines, 'guide/items.md', 'Admin', { wikilink: false });
  assert.deepEqual(
    verdict,
    { kind: 'unverifiable' },
    `expected the whole scan to decline (an otherwise-clean 'Admin' container cannot rescue this), got ${JSON.stringify(verdict)}`,
  );
});

test('verifyNonHeadingPlacement whole-scan-abort [round-13, BLOCKER 2c]: contrast — WITHOUT the extra non-plain row, the same clean container reports ok', () => {
  const indexLines = ['- Admin', '  - [Items](<guide/items.md>)'];
  const verdict = verifyNonHeadingPlacement(indexLines, 'guide/items.md', 'Admin', { wikilink: false });
  assert.deepEqual(verdict, { kind: 'ok' }, `expected ok without the extra row, got ${JSON.stringify(verdict)}`);
});

test('verifyNonHeadingPlacement whole-scan-abort [round-13, BLOCKER 2c]: the complement — a non-plain CHILD label (not indent-0) does NOT decline the scan', () => {
  // The chapter's own row is correctly nested under 'Admin'; a SECOND, unrelated child under the
  // SAME container carries a non-plain label ('*Other Child*') — isPlainLabel is a CONTAINER-only
  // (indent-0) gate, so a non-plain label at indent 1 must not trigger the whole-scan decline. This
  // is what makes the claim about indent-0 specifically, not about labels in general.
  const indexLines = ['- Admin', '  - [Items](<guide/items.md>)', '  - *Other Child*'];
  const verdict = verifyNonHeadingPlacement(indexLines, 'guide/items.md', 'Admin', { wikilink: false });
  assert.deepEqual(
    verdict,
    { kind: 'ok' },
    `expected a non-plain CHILD label not to decline the scan, got ${JSON.stringify(verdict)}`,
  );
});

// =================================================================================================
// D1 — validateGroups
// =================================================================================================

test('validateGroups: group regex rejects uppercase, slash, spaces, trailing hyphen', () => {
  const cases = ['Admin', 'a/b', 'a b', 'a-'];
  for (const bad of cases) {
    const halts = validateGroups([entry({ group: bad, group_title: 'T' })]);
    assert.ok(
      halts.includes(`Invalid group '${bad}' — group must be English kebab-case, one level (no '/').`),
      `expected an invalid-group halt for '${bad}'`,
    );
  }
});

test('validateGroups: reserved group name and reserved slug (grouped manifest)', () => {
  const halts = validateGroups([
    entry({ slug: 'x', group: 'assets', group_title: 'X Group' }),
    entry({ slug: 'assets', group: 'g2', group_title: 'G2' }),
  ]);
  assert.ok(halts.includes(`group 'assets' is reserved (co-location follow-up; keeps the tree unambiguous).`));
  assert.ok(
    halts.includes(`slug 'assets' is reserved in a grouped manifest (co-location follow-up; keeps the tree unambiguous).`),
  );
});

test('validateGroups: duplicate slug across groups (global uniqueness)', () => {
  const halts = validateGroups([
    entry({ slug: 'x', group: 'a', group_title: 'A' }),
    entry({ slug: 'x', group: 'b', group_title: 'B' }),
  ]);
  assert.deepEqual(halts, [
    `Duplicate chapter slug 'x' — chapter slugs must be globally unique across all groups (chapter basenames stay unambiguous across the handbook for the file tree, user-authored bare wikilinks, and Quartz-shortest bare-name resolution).`,
  ]);
});

test('validateGroups: THREE-occurrence duplicate slug still halts exactly ONCE [round-12]', () => {
  // Round-12 finding: every duplicate fixture in the suite (this one included, until now) used
  // exactly two occurrences, so `duplicateSlugHalts`'s `count > 1` boundary was indistinguishable
  // from `count === 2` — a mutant narrowing to `=== 2` restores #221's silent overwrite for any
  // manifest with a TRIPLICATED slug, in both manifest kinds. A triplicate additionally proves the
  // Map-keyed gate emits exactly ONE halt per distinct slug, not one per extra occurrence.
  const halts = validateGroups([
    entry({ slug: 'x', group: 'a', group_title: 'A' }),
    entry({ slug: 'x', group: 'b', group_title: 'B' }),
    entry({ slug: 'x', group: 'c', group_title: 'C' }),
  ]);
  assert.deepEqual(halts, [
    `Duplicate chapter slug 'x' — chapter slugs must be globally unique across all groups (chapter basenames stay unambiguous across the handbook for the file tree, user-authored bare wikilinks, and Quartz-shortest bare-name resolution).`,
  ]);
});

test('validateGroups: duplicate-slug gate sees the FULL entry list in an anyGroup manifest — flat-vs-flat AND grouped-vs-flat pairs [round-18]', () => {
  // Round-18 finding: `validateGroups`'s grouped branch, gate 3, calls `duplicateSlugHalts(entries,
  // ...)` on the FULL entry list — every existing duplicate-in-a-grouped-
  // manifest fixture used ONLY grouped entries for the duplicated slug, so a mutant filtering to
  // `entries.filter(e => e.group !== undefined)` before the call stayed fully green. That mutant
  // silently stops checking flat entries in an anyGroup manifest: a grouped-vs-flat slug
  // collision (the exact case "globally unique across all groups" exists to catch) AND a
  // flat-vs-flat collision (neither party grouped) both go undetected. One fixture with 'p'
  // (flat-vs-flat) and 'r' (one grouped occurrence + one flat occurrence) proves both categories
  // at once — filtering out flat entries removes ALL of 'p's occurrences and one of 'r's two,
  // leaving neither above the duplicate threshold.
  const halts = validateGroups([
    entry({ slug: 'q', group: 'g1', group_title: 'G1' }), // grouped anchor, keeps anyGroup true
    entry({ slug: 'p' }), // flat #1 — flat-vs-flat pair
    entry({ slug: 'p' }), // flat #2 — flat-vs-flat pair
    entry({ slug: 'r', group: 'g2', group_title: 'G2' }), // grouped
    entry({ slug: 'r' }), // flat — grouped-vs-flat pair (same slug 'r')
  ]);
  assert.deepEqual(halts, [
    `Duplicate chapter slug 'p' — chapter slugs must be globally unique across all groups (chapter basenames stay unambiguous across the handbook for the file tree, user-authored bare wikilinks, and Quartz-shortest bare-name resolution).`,
    `Duplicate chapter slug 'r' — chapter slugs must be globally unique across all groups (chapter basenames stay unambiguous across the handbook for the file tree, user-authored bare wikilinks, and Quartz-shortest bare-name resolution).`,
  ]);
});

test('validateGroups: group-vs-flat-slug collision', () => {
  const halts = validateGroups([entry({ slug: 'a', group: 'g', group_title: 'G' }), entry({ slug: 'g' })]);
  assert.deepEqual(halts, [
    `group 'g' collides with flat chapter slug 'g' — a directory and a chapter file cannot share the same path under publish.chapters_dir.`,
  ]);
});

test('validateGroups: missing group_title', () => {
  const halts = validateGroups([entry({ slug: 'a', group: 'g' })]);
  assert.deepEqual(halts, [
    `Entry 'a' in group 'g' lacks group_title — every grouped entry carries the localized group title (never derived from the English group slug).`,
  ]);
});

test('validateGroups: intra-group conflicting group_title', () => {
  const halts = validateGroups([
    entry({ slug: 'a', group: 'g', group_title: 'Alpha' }),
    entry({ slug: 'b', group: 'g', group_title: 'Beta' }),
  ]);
  assert.deepEqual(halts, [
    `Group 'g' carries conflicting group_title values ('Alpha', 'Beta') — align all entries of the group.`,
  ]);
});

test('validateGroups: THREE distinct group_titles in one group still halts, not silently accepted [round-13 audit]', () => {
  // Round-13 audit finding: the only conflicting-title fixture uses exactly 2 distinct titles,
  // so `distinctTitles.length > 1` was indistinguishable from `=== 2`. A third distinct title
  // proves the halt still fires (a `=== 2` mutant would silently accept a 3-way-inconsistent
  // group). The halt now enumerates EVERY distinct title in the message, so all three — including
  // 'Gamma' — appear, not just the first two.
  const halts = validateGroups([
    entry({ slug: 'a', group: 'g', group_title: 'Alpha' }),
    entry({ slug: 'b', group: 'g', group_title: 'Beta' }),
    entry({ slug: 'c', group: 'g', group_title: 'Gamma' }),
  ]);
  assert.deepEqual(halts, [
    `Group 'g' carries conflicting group_title values ('Alpha', 'Beta', 'Gamma') — align all entries of the group.`,
  ]);
});

test('validateGroups: cross-group shared group_title', () => {
  const halts = validateGroups([
    entry({ slug: 'a', group: 'g1', group_title: 'Same' }),
    entry({ slug: 'b', group: 'g2', group_title: 'Same' }),
  ]);
  assert.deepEqual(halts, [
    `Groups 'g1' and 'g2' share group_title 'Same' — nav containers are located by title; give each group a distinct localized title.`,
  ]);
});

test('R2-F5: numeric / whitespace-only / non-string group_title all hit the EXISTING missing-title halt (no new halt string)', () => {
  for (const bad of [123, '   ', true, null]) {
    const halts = validateGroups([entry({ slug: 'a', group: 'g', group_title: bad })]);
    assert.ok(
      halts.includes(
        `Entry 'a' in group 'g' lacks group_title — every grouped entry carries the localized group title (never derived from the English group slug).`,
      ),
      `expected the missing-title halt for group_title=${JSON.stringify(bad)}, got: ${JSON.stringify(halts)}`,
    );
  }
});

test('R2-F5: padding-only differences within a group do not spuriously trigger the conflicting-titles halt', () => {
  const halts = validateGroups([
    entry({ slug: 'a', group: 'g', group_title: 'Admin' }),
    entry({ slug: 'b', group: 'g', group_title: '  Admin  ' }),
  ]);
  assert.deepEqual(halts, []);
});

// =================================================================================================
// #310 [1.9.0] — validateGroups per-group slug uniqueness opt-in (publish.per_group_slug_uniqueness)
// =================================================================================================
// The opt-in scopes slug uniqueness PER GROUP: two chapters in DIFFERENT groups may reuse a slug
// (distinct group subdirectories ⇒ no file-tree collision), but a duplicate WITHIN one group still
// halts. Default (option absent / false) is byte-for-byte the pre-1.9.0 global-uniqueness gate —
// the existing 1-arg validateGroups tests above (global-uniqueness, three-occurrence, round-18)
// remain the default-off proof.

// Scenario 1 (primary discriminator): different-group same-slug ⇒ NO halt under the opt-in. RED
// against pre-1.9.0 code, which ignores the option and always halts on a repeated slug.
test('#310 opt-in: different-group same-slug does NOT halt', () => {
  const halts = validateGroups(
    [
      entry({ slug: 'x', group: 'a', group_title: 'A' }),
      entry({ slug: 'x', group: 'b', group_title: 'B' }),
    ],
    { perGroupSlugs: true },
  );
  assert.deepEqual(halts, []);
});

// Scenario 2: same-group same-slug ⇒ halts with the NEW group-scoped literal (S1). RED against
// pre-1.9.0 (returns the global-uniqueness literal, not this one).
test('#310 opt-in: same-group same-slug halts with the group-scoped literal', () => {
  const halts = validateGroups(
    [
      entry({ slug: 'x', group: 'a', group_title: 'A' }),
      entry({ slug: 'x', group: 'a', group_title: 'A' }),
    ],
    { perGroupSlugs: true },
  );
  assert.deepEqual(halts, [
    `Duplicate chapter slug 'x' within group 'a' — with publish.per_group_slug_uniqueness enabled, chapter slugs must be unique within each group; a duplicate silently overwrites the chapter file and its asset dir.`,
  ]);
});

// Mutant guard (group-only key): a mutant keying on the group alone (dropping the slug) would
// falsely halt two DISTINCT slugs sharing a group. Same-group / different-slug under the opt-in
// must stay clean. (Green before and after — a mutant killer, not a red-before-green discriminator.)
test('#310 opt-in: same-group DIFFERENT slugs do not halt (kills the group-only key mutant)', () => {
  const halts = validateGroups(
    [
      entry({ slug: 'x', group: 'a', group_title: 'A' }),
      entry({ slug: 'y', group: 'a', group_title: 'A' }),
    ],
    { perGroupSlugs: true },
  );
  assert.deepEqual(halts, []);
});

// Scenario 4: flat `items` vs grouped `admin/items` (same basename) ⇒ NO halt under the opt-in —
// distinct namespaces (flat keys the bare slug; a grouped entry keys `<group><NUL><slug>`). RED
// against pre-1.9.0 (option ignored ⇒ slug 'items' seen twice ⇒ global halt).
test('#310 opt-in: flat slug vs grouped same-basename do not collide', () => {
  const halts = validateGroups(
    [entry({ slug: 'items' }), entry({ slug: 'items', group: 'admin', group_title: 'Admin' })],
    { perGroupSlugs: true },
  );
  assert.deepEqual(halts, []);
});

// Scenario 3a: even under the opt-in, a GROUP-FREE manifest's duplicate flat slug still halts with
// the unchanged group-free literal (326) — the option is threaded into the group-free branch but
// inert there (no entry carries a group). Unchanged behavior ⇒ green before and after.
test('#310 opt-in inert on a group-free manifest: duplicate flat slug still halts (326 literal)', () => {
  const halts = validateGroups([entry({ slug: 'f' }), entry({ slug: 'f' })], { perGroupSlugs: true });
  assert.deepEqual(halts, [
    `Duplicate chapter slug 'f' — chapter slugs must be unique; a duplicate silently overwrites the chapter file and its asset dir.`,
  ]);
});

// Scenario 3b: within a GROUPED manifest under the opt-in, a flat-vs-flat pair still keys the bare
// slug and halts with the UNCHANGED global-uniqueness literal (327 / S2) — flat entries share one
// file-tree namespace regardless of the opt-in. Unchanged behavior ⇒ green before and after.
test('#310 opt-in: flat-vs-flat pair in a grouped manifest still halts globally (327/S2 literal)', () => {
  const halts = validateGroups(
    [
      entry({ slug: 'k', group: 'g', group_title: 'G' }), // grouped anchor keeps anyGroup true
      entry({ slug: 'f' }),
      entry({ slug: 'f' }),
    ],
    { perGroupSlugs: true },
  );
  assert.deepEqual(halts, [
    `Duplicate chapter slug 'f' — chapter slugs must be globally unique across all groups (chapter basenames stay unambiguous across the handbook for the file tree, user-authored bare wikilinks, and Quartz-shortest bare-name resolution).`,
  ]);
});

// Default-off proof: the exact different-group same-slug manifest scenario 1 CLEARS under the
// opt-in STILL halts globally when the option is absent / {} / explicitly false — the opt-in is
// genuinely opt-in and option-absent callers are byte-for-byte unchanged. (Complements the 1-arg
// existing tests above; also kills an "always-on" mutant that treats perGroupSlugs as true.)
test('#310 default (option absent/false): different-group same-slug still halts globally', () => {
  const entries = [
    entry({ slug: 'x', group: 'a', group_title: 'A' }),
    entry({ slug: 'x', group: 'b', group_title: 'B' }),
  ];
  const expected = [
    `Duplicate chapter slug 'x' — chapter slugs must be globally unique across all groups (chapter basenames stay unambiguous across the handbook for the file tree, user-authored bare wikilinks, and Quartz-shortest bare-name resolution).`,
  ];
  assert.deepEqual(validateGroups(entries), expected);
  assert.deepEqual(validateGroups(entries, {}), expected);
  assert.deepEqual(validateGroups(entries, { perGroupSlugs: false }), expected);
});

// FIX-2: a MALFORMED group (blank YAML `group:` ⇒ null) under the opt-in must NOT be keyed
// per-group — otherwise a duplicate renders a misleading "within group 'null'" literal, and null
// vs '' alias onto one `<NUL><slug>` bucket. The tightened predicate (GROUP_PATTERN, the gate-1
// validator) makes it fall back to the bare-slug (global) key; gate 1 remains the sole group-level
// halt. Two null-group same-slug entries would, under the OLD `!== undefined` predicate, emit the
// per-group S1 literal ⇒ this test is RED without the fix.
test("#310 FIX-2: malformed (null) group under the opt-in never renders a per-group literal; gate 1 still halts", () => {
  const halts = validateGroups(
    [
      entry({ slug: 'a', group: null, group_title: 'T' }),
      entry({ slug: 'a', group: null, group_title: 'T' }),
    ],
    { perGroupSlugs: true },
  );
  assert.ok(
    !halts.some((h) => h.includes('within group')),
    `a malformed group must not take the per-group S1 literal; got ${JSON.stringify(halts)}`,
  );
  assert.ok(
    halts.includes(`Invalid group 'null' — group must be English kebab-case, one level (no '/').`),
    'gate 1 is still the halt that fires for a malformed group',
  );
});

// FIX-3: the NUL key separator is alias-free. Boundary values chosen so a separator-LESS
// `group+slug` join would collapse both entries to the same string `"abc"` and falsely halt:
// group 'a' + slug 'bc' vs group 'ab' + slug 'c'. The real NUL join keys them `a<NUL>bc` vs
// `ab<NUL>c` — DISTINCT ⇒ no duplicate. GREEN with the real impl; a no-separator mutant goes RED.
test('#310 FIX-3: NUL key separator is alias-free — a|bc vs ab|c do not collide', () => {
  const halts = validateGroups(
    [
      entry({ slug: 'bc', group: 'a', group_title: 'A' }),
      entry({ slug: 'c', group: 'ab', group_title: 'AB' }),
    ],
    { perGroupSlugs: true },
  );
  assert.deepEqual(halts, []);
});

test('R2-F5: a padded-but-valid group_title converges against an existing heading (findContainer trims its own param)', () => {
  const result = findContainer(['## Admin', '- x'], '  Admin  ');
  assert.equal(result.kind, 'single');
  assert.equal(result.location.title, 'Admin');
});

test('R2-F5: the halt record renders the TRIMMED title, never the raw padded value', () => {
  const p = profile();
  const old = entry({ group: 'a', group_title: '  Admin  ' });
  const next = entry({ group: 'b', group_title: '  Admin  ' });
  const facts = manualMigrationChecklist(p, old, next);
  const changes = [{ kind: 'group-change', slug: 'items', oldEntry: old, newEntry: next }];
  const text = renderManualMigrationHalt(changes, [facts]);
  assert.match(text, /was under container 'Admin'/);
  assert.ok(!text.includes('  Admin  '), 'the raw padded title must never appear in the rendered halt');
});

test('validateGroups: a clean grouped manifest => []', () => {
  const halts = validateGroups([
    entry({ slug: 'a', group: 'g1', group_title: 'G1' }),
    entry({ slug: 'b', group: 'g2', group_title: 'G2' }),
    entry({ slug: 'c' }),
  ]);
  assert.deepEqual(halts, []);
});

test('#221 activation pin [1.6.0]: a group-free manifest with a duplicated flat slug now HALTS unconditionally', () => {
  // Inverts the pre-1.6.0 "[] (unchanged 1.4.1 behavior)" pin — #221 removes the profile opt-out;
  // a group-free duplicate flat slug is no longer the silent-overwrite 1.4.1 behavior.
  const halts = validateGroups([entry({ slug: 'x' }), entry({ slug: 'x' })]);
  assert.deepEqual(halts, [
    `Duplicate chapter slug 'x' — chapter slugs must be unique; a duplicate silently overwrites the chapter file and its asset dir.`,
  ]);
});

test('#221: THREE-occurrence duplicate flat slug still halts exactly ONCE [round-12]', () => {
  // Companion to the grouped triplicate pin above — the group-free branch of duplicateSlugHalts
  // shares the SAME `count > 1` boundary, so it is equally vulnerable to the `=== 2` narrowing
  // under a 3-occurrence manifest, restoring the silent overwrite this whole issue exists to fix.
  const halts = validateGroups([entry({ slug: 'x' }), entry({ slug: 'x' }), entry({ slug: 'x' })]);
  assert.deepEqual(halts, [
    `Duplicate chapter slug 'x' — chapter slugs must be unique; a duplicate silently overwrites the chapter file and its asset dir.`,
  ]);
});

test('#221: a clean group-free manifest still returns []', () => {
  const halts = validateGroups([entry({ slug: 'a' }), entry({ slug: 'b' })]);
  assert.deepEqual(halts, []);
});

test('#221 single-gate boundary pin [round-11]: a group-free {slug: "assets"} must NOT trip the grouped reserved-slug gate', () => {
  // Round-11 finding: `validateGroups` early-returns `duplicateSlugHalts(entries, {groupFree:
  // true})` for a group-free manifest — gates 1, 2, 4, 5, 6 run only inside the `anyGroup`
  // branch. Every existing group-free fixture uses ordinary slugs, so a refactor that
  // accumulates ALL gates unconditionally (computing `groupFree` only to pick the duplicate
  // literal) stays fully green. That refactor would wrongly reject a LEGITIMATE group-free
  // manifest containing a chapter slugged 'assets' through gate 2's grouped-only reserved-slug
  // check — a false halt on valid input, exactly the direction users would actually hit it.
  const halts = validateGroups([{ slug: 'assets' }]);
  assert.deepEqual(halts, []);
});

test('#221 single-gate boundary pin [round-11]: duplicate "assets" in a group-free manifest emits ONLY the group-free duplicate literal', () => {
  // Companion to the clean-manifest case above: pins the OTHER half of the same boundary. Here a
  // halt IS expected (the slug really is duplicated), so this proves the grouped-only gate 2
  // still does not leak in ALONGSIDE the correct group-free duplicate halt — not just that it
  // stays silent on a fully clean manifest.
  const halts = validateGroups([{ slug: 'assets' }, { slug: 'assets' }]);
  assert.deepEqual(halts, [
    `Duplicate chapter slug 'assets' — chapter slugs must be unique; a duplicate silently overwrites the chapter file and its asset dir.`,
  ]);
});

test('#221: multiple group-free duplicate slugs halt in first-seen (Map insertion) order', () => {
  const halts = validateGroups([
    entry({ slug: 'b' }),
    entry({ slug: 'a' }),
    entry({ slug: 'b' }),
    entry({ slug: 'a' }),
  ]);
  assert.deepEqual(halts, [
    `Duplicate chapter slug 'b' — chapter slugs must be unique; a duplicate silently overwrites the chapter file and its asset dir.`,
    `Duplicate chapter slug 'a' — chapter slugs must be unique; a duplicate silently overwrites the chapter file and its asset dir.`,
  ]);
});

test('#221: grouped halt set AND emission order stay byte-unchanged from 1.5.0 (gates 1,2,duplicate,4,5,6, ALL SIX AT ONCE)', () => {
  // One manifest, one violation per gate, none overlapping — proves the duplicateSlugHalts
  // extraction did not move gate 3 relative to ANY of its five neighbors (a weaker fixture
  // hitting only gates 2/3/6 could not detect the duplicate gate sliding across gates 1, 4, or 5).
  //   1 'Bad Group'      -> invalid group (kebab violation)
  //   2 slug 'assets'    -> reserved slug in a grouped manifest
  //   3 slug 'dup-slug'  -> duplicate across groups g-dup-a/g-dup-b
  //   4 group 'flatclash'-> collides with a flat entry of the same slug
  //   5 group 'g-missing-title' -> entry with no group_title
  //   6 groups 'g-shared-1'/'g-shared-2' -> share group_title 'SharedTitle'
  const halts = validateGroups([
    entry({ slug: 'e1', group: 'Bad Group', group_title: 'T1' }),
    entry({ slug: 'assets', group: 'g-reserved-slug', group_title: 'T2' }),
    entry({ slug: 'dup-slug', group: 'g-dup-a', group_title: 'T3a' }),
    entry({ slug: 'dup-slug', group: 'g-dup-b', group_title: 'T3b' }),
    entry({ slug: 'e4', group: 'flatclash', group_title: 'T4' }),
    entry({ slug: 'flatclash' }),
    entry({ slug: 'e5', group: 'g-missing-title' }),
    entry({ slug: 'e6a', group: 'g-shared-1', group_title: 'SharedTitle' }),
    entry({ slug: 'e6b', group: 'g-shared-2', group_title: 'SharedTitle' }),
  ]);
  assert.deepEqual(halts, [
    `Invalid group 'Bad Group' — group must be English kebab-case, one level (no '/').`,
    `slug 'assets' is reserved in a grouped manifest (co-location follow-up; keeps the tree unambiguous).`,
    `Duplicate chapter slug 'dup-slug' — chapter slugs must be globally unique across all groups (chapter basenames stay unambiguous across the handbook for the file tree, user-authored bare wikilinks, and Quartz-shortest bare-name resolution).`,
    `group 'flatclash' collides with flat chapter slug 'flatclash' — a directory and a chapter file cannot share the same path under publish.chapters_dir.`,
    `Entry 'e5' in group 'g-missing-title' lacks group_title — every grouped entry carries the localized group title (never derived from the English group slug).`,
    `Groups 'g-shared-1' and 'g-shared-2' share group_title 'SharedTitle' — nav containers are located by title; give each group a distinct localized title.`,
  ]);
});

test('F1: non-string / non-kebab group values (null, false, 0, 123, "") all halt as Invalid group', () => {
  // A regex .test() coerces its argument to a string, so null/false/0/123 would otherwise
  // stringify to "null"/"false"/"0"/"123" and silently PASS as "valid" kebab strings — the
  // explicit typeof check closes that. null is deliberately treated as PRESENT-and-invalid (a
  // blank YAML `group:` parses to null and must be a visible halt, never silently flat).
  for (const bad of [null, false, 0, 123, '']) {
    const entries = [entry({ group: bad, group_title: 'T' })];
    const halts = validateGroups(entries);
    assert.ok(
      halts.some((h) => h.startsWith(`Invalid group '${bad}'`)),
      `expected an Invalid-group halt for group=${JSON.stringify(bad)}, got: ${JSON.stringify(halts)}`,
    );
  }
});

test('F1: anyGroup/derivation consistency — a present-but-invalid group is never silently treated as flat', () => {
  // anyGroup and validateGroups both use `!== undefined` as "present"; chapterRelPath/
  // chapterAssetDir must use the SAME predicate (not truthiness), so a falsy-but-present group
  // (0, false, null) can never disagree with anyGroup's verdict and silently derive a flat path.
  //
  // Round-13 audit — DOCUMENTED ASYMMETRY, not a gap: this is the ONLY direct `anyGroup(...)`
  // call in the file, and it only ever asserts the `true` branch (a present-but-falsy group).
  // No direct call anywhere asserts `anyGroup(...) === false`. Traced both a hardcode-always-true
  // and a hardcode-always-false mutation of `anyGroup`'s own body: both are caught, but only
  // TRANSITIVELY — through `validateGroups`'s early-return branch selection and its resulting
  // halt literal (a hardcoded-true `anyGroup` makes a clean group-free manifest take the grouped
  // gate path; a hardcoded-false one makes a genuinely grouped manifest silently skip gates
  // 1/2/4/5/6 entirely). Left as-is deliberately — see the audit report for the full trace. If
  // `anyGroup` ever grows a caller that does NOT route through `validateGroups`, that caller
  // needs its own direct true/false coverage; this comment is the flag for that day.
  for (const bad of [null, false, 0]) {
    const e = entry({ group: bad, slug: 'x' });
    assert.equal(anyGroup([e]), true, `anyGroup must treat group=${JSON.stringify(bad)} as present`);
    assert.equal(
      chapterRelPath(e),
      `${bad}/x.md`,
      `chapterRelPath must derive the GROUPED form for group=${JSON.stringify(bad)}, not silently flat`,
    );
  }
});

// =================================================================================================
// D6 — groupChanges
// =================================================================================================

test('groupChanges: group added on a retained (flat -> grouped) entry', () => {
  const old = [entry()];
  const next = [entry({ group: 'g', group_title: 'G' })];
  const { changes } = groupChanges(old, next);
  assert.equal(changes.length, 1);
  assert.equal(changes[0].kind, 'group-change');
});

test('groupChanges: group removed on a retained (grouped -> flat) entry', () => {
  const old = [entry({ group: 'g', group_title: 'G' })];
  const next = [entry()];
  const { changes } = groupChanges(old, next);
  assert.equal(changes.length, 1);
  assert.equal(changes[0].kind, 'group-change');
});

test('groupChanges: group changed (grouped -> grouped, same title) on a retained entry', () => {
  const old = [entry({ group: 'g1', group_title: 'T' })];
  const next = [entry({ group: 'g2', group_title: 'T' })];
  const { changes } = groupChanges(old, next);
  assert.equal(changes[0].kind, 'group-change');
});

test('groupChanges: group_title-only change on a retained grouped entry', () => {
  const old = [entry({ group: 'g', group_title: 'Old' })];
  const next = [entry({ group: 'g', group_title: 'New' })];
  const { changes } = groupChanges(old, next);
  assert.equal(changes.length, 1);
  assert.equal(changes[0].kind, 'title-change');
});

test('groupChanges: group AND title both change (both sides grouped) => combined kind', () => {
  const old = [entry({ group: 'g1', group_title: 'T1' })];
  const next = [entry({ group: 'g2', group_title: 'T2' })];
  const { changes } = groupChanges(old, next);
  assert.equal(changes[0].kind, 'group-and-title-change');
});

test('R9-F2 domain pin: pure new-entry addition => NO migration kind, even when it flips anyGroup', () => {
  const old = [entry({ slug: 'a' })];
  const next = [entry({ slug: 'a' }), entry({ slug: 'b', group: 'g', group_title: 'G' })];
  const result = groupChanges(old, next);
  assert.deepEqual(result.changes, []);
  assert.equal(result.anyGroupFlip, true);
});

test('groupChanges: a GROUPED old-only entry => removal kind', () => {
  const old = [entry({ slug: 'a', group: 'g', group_title: 'G' })];
  const { changes } = groupChanges(old, []);
  assert.equal(changes.length, 1);
  assert.equal(changes[0].kind, 'removal');
  assert.equal(changes[0].newEntry, null);
});

test('R5 F4 pin: removing the ONLY grouped entry emits BOTH the removal kind AND anyGroupFlip:true — flip never suppresses kinds', () => {
  const old = [entry({ slug: 'a', group: 'g', group_title: 'G' })];
  const result = groupChanges(old, []);
  assert.equal(result.changes.length, 1);
  assert.equal(result.changes[0].kind, 'removal');
  assert.equal(result.anyGroupFlip, true, 'anyGroup(old)=true -> anyGroup([])=false is a genuine flip');
});

test('groupChanges: a FLAT old-only entry => no kind', () => {
  const old = [entry({ slug: 'a' })];
  const { changes } = groupChanges(old, []);
  assert.deepEqual(changes, []);
});

test('groupChanges: a multi-group manifest losing ONE group => per-entry kind only, NO flip', () => {
  const old = [
    entry({ slug: 'a', group: 'g1', group_title: 'G1' }),
    entry({ slug: 'b', group: 'g2', group_title: 'G2' }),
  ];
  const next = [entry({ slug: 'a', group: 'g1', group_title: 'G1' })];
  const result = groupChanges(old, next);
  assert.equal(result.changes.length, 1);
  assert.equal(result.changes[0].kind, 'removal');
  assert.equal(result.anyGroupFlip, false);
});

test('groupChanges: identical manifests => empty', () => {
  const entries = [entry({ slug: 'a', group: 'g', group_title: 'G' })];
  const result = groupChanges(entries, entries);
  assert.deepEqual(result.changes, []);
  assert.equal(result.anyGroupFlip, false);
});

test('activation pin: group-free -> group-free edits => empty + no flip', () => {
  const old = [{ slug: 'a', title: 'X' }];
  const next = [{ slug: 'a', title: 'Y' }];
  const result = groupChanges(old, next);
  assert.deepEqual(result.changes, []);
  assert.equal(result.anyGroupFlip, false);
});

// =================================================================================================
// #295 — currentIndexExpectedTarget (direct, exported)
// =================================================================================================

test('#295 currentIndexExpectedTarget: PATH mode, flat entry — 3rd arg omitted, path mode ignores it', () => {
  const p = profile();
  assert.equal(currentIndexExpectedTarget(p, entry()), 'handbook/items.md');
});

test('#295 currentIndexExpectedTarget: PATH mode, grouped entry', () => {
  const p = profile();
  assert.equal(
    currentIndexExpectedTarget(p, entry({ group: 'admin' })),
    'handbook/admin/items.md',
  );
});

test('#294/§1a currentIndexExpectedTarget: WIKILINKS mode, flat entry — vault-root-relative, .md dropped', () => {
  const p = profile({ publish: { wikilinks: true } });
  assert.equal(currentIndexExpectedTarget(p, entry(), 'handbook'), 'handbook/items');
});

test('#294/§1a currentIndexExpectedTarget: WIKILINKS mode, grouped entry — group rides on the target (unlike the pre-1.8.0 bare slug)', () => {
  const p = profile({ publish: { wikilinks: true } });
  assert.equal(
    currentIndexExpectedTarget(p, entry({ group: 'admin' }), 'handbook'),
    'handbook/admin/items',
  );
});

test('§0a root topology: WIKILINKS mode, vaultRelChaptersDir \'\' (chapters_dir IS the vault root) — flat entry', () => {
  const p = profile({ publish: { wikilinks: true } });
  assert.equal(currentIndexExpectedTarget(p, entry(), ''), 'items', 'single-segment true vault-root path');
});

test('§1a codex R2 BLOCKER-1 symlink-subdir topology: a multi-segment precomputed prefix joins correctly', () => {
  const p = profile({ publish: { wikilinks: true } });
  assert.equal(
    currentIndexExpectedTarget(p, entry(), 'subdir/handbook'),
    'subdir/handbook/items',
    'the precomputed-prefix contract: the adapter, not this pure helper, resolves the symlink',
  );
});

test('§1a fail-loud guard: WIKILINKS mode with vaultRelChaptersDir omitted/null throws (no silent bare-slug fallback)', () => {
  const p = profile({ publish: { wikilinks: true } });
  assert.throws(() => currentIndexExpectedTarget(p, entry()), /vaultRelChaptersDir is required/);
  assert.throws(() => currentIndexExpectedTarget(p, entry(), null), /vaultRelChaptersDir is required/);
});

test('§1a fail-loud guard: WIKILINKS mode with an ABSOLUTE vaultRelChaptersDir throws', () => {
  const p = profile({ publish: { wikilinks: true } });
  assert.throws(() => currentIndexExpectedTarget(p, entry(), '/v'), /must be vault-root-relative/);
});

test('§1a fail-loud guard: WIKILINKS mode with a \'..\'-escaping vaultRelChaptersDir throws', () => {
  const p = profile({ publish: { wikilinks: true } });
  assert.throws(() => currentIndexExpectedTarget(p, entry(), '../x'), /escapes the vault root/);
});

// =================================================================================================
// D6 — manualMigrationChecklist
// =================================================================================================

test('manualMigrationChecklist: retained group-change facts (current path/dir/index, capture-spec, old-gone) — PATH-LINK mode', () => {
  // profile() defaults to wikilinks: false — path mode. Even with the title preserved on both
  // sides ('Admin' -> 'Admin'), old and new PATH targets are textually different strings (the
  // relative path changed), so the R14-F3 exactly-one exception must NOT apply here (F2) — the
  // sound fact is zero old-target matches, same as any other path-mode move.
  const p = profile();
  const old = entry({ group: 'admin', group_title: 'Admin' });
  const next = entry({ group: 'management', group_title: 'Admin' });
  const facts = manualMigrationChecklist(p, old, next);

  assert.equal(findFact(facts, 'current-chapter-path').path, 'vault/handbook/management/items.md');
  assert.equal(findFact(facts, 'current-asset-dir').path, 'vault/handbook/assets/management/items');
  const membership = findFact(facts, 'current-index-membership');
  assert.equal(membership.expectedTarget, 'handbook/management/items.md');
  assert.equal(membership.containerTitle, 'Admin');
  const specCheck = findFact(facts, 'capture-spec-check');
  assert.equal(specCheck.oldDirQualified, 'vault/handbook/assets/admin/items');
  assert.equal(specCheck.oldDirTail, 'admin/items');
  assert.equal(findFact(facts, 'old-chapter-path-gone').path, 'vault/handbook/admin/items.md');
  assert.equal(findFact(facts, 'old-asset-dir-gone').path, 'vault/handbook/assets/admin/items');
  const oldTarget = findFact(facts, 'old-index-target-gone');
  assert.equal(oldTarget.form, 'path');
  assert.equal(oldTarget.oldContainerTitle, 'Admin');
  assert.equal(oldTarget.legacyBareTarget, undefined, 'path mode never carries a legacy-bare fact');
  assert.equal(findFact(facts, 'title-container'), undefined, 'unchanged title carries no title fact');
});

test('#253: manualMigrationChecklist derives each fact from its OWN root — decoupled output_dir/chapters_dir/index_file', () => {
  // Every prior fixture keeps the three roots in a FIXED relationship (output_dir = chapters_dir +
  // '/assets'; index_file = chapters_dir's parent + '/SUMMARY.md'), so a cross-substitution among
  // the three roots would shape-match. These three roots share NO common prefix and no derivable
  // relationship, so any root cross-substitution is caught — one fixture kills all three at once.
  const p = profile({
    capture: { output_dir: 'shots' },
    publish: { chapters_dir: 'book/pages', index_file: 'toc/SUMMARY.md' },
  });
  const old = entry({ group: 'admin', group_title: 'Admin' });
  const next = entry({ group: 'management', group_title: 'Admin' });
  const facts = manualMigrationChecklist(p, old, next);

  assert.equal(findFact(facts, 'current-chapter-path').path, 'book/pages/management/items.md');
  assert.equal(findFact(facts, 'current-asset-dir').path, 'shots/management/items');
  assert.equal(findFact(facts, 'current-index-membership').expectedTarget, '../book/pages/management/items.md');
});

test('#294 group-slug move, WIKILINK mode: old vault-rel target is expected GONE + carries legacyBareTarget (R14-F3 exception dropped)', () => {
  // Same title-preserving group-slug move as the path-mode fixture above, but wikilinks: true.
  // Under Option A the vault-rel target is `<vaultRelChaptersDir>/<group>/<slug>`, so old and new
  // ARE different strings even though group_title is preserved (`handbook/admin/items` !=
  // `handbook/management/items`) — the pre-1.8.0 "exactly one match under the shared container"
  // exception (R14-F3) has no live case under this formula and is gone; the old QUALIFIED target
  // is always expected GONE. A separate `legacyBareTarget` fact carries the bare pre-1.8.0 slug
  // for the container-scoped legacy-bare-gone check (§1b BLOCKER-2a).
  const p = profile({ publish: { wikilinks: true } });
  const old = entry({ group: 'admin', group_title: 'Admin' });
  const next = entry({ group: 'management', group_title: 'Admin' });
  const facts = manualMigrationChecklist(p, old, next, 'handbook');

  const oldTarget = findFact(facts, 'old-index-target-gone');
  assert.equal(oldTarget.form, 'wikilink');
  assert.equal(oldTarget.oldContainerTitle, 'Admin');
  assert.equal(oldTarget.expectedTarget, 'handbook/admin/items', 'old qualified target, GONE');
  assert.equal(oldTarget.legacyBareTarget, 'items');
});

test('R9-F5/R12-F2 grouped -> flat retained entry: flat-placement facts, NO title fact', () => {
  const p = profile();
  const old = entry({ group: 'admin', group_title: 'Admin' });
  const next = entry();
  const facts = manualMigrationChecklist(p, old, next);

  assert.equal(findFact(facts, 'current-chapter-path').path, 'vault/handbook/items.md');
  assert.equal(findFact(facts, 'current-asset-dir').path, 'vault/handbook/assets/items');
  const flatMembership = findFact(facts, 'flat-membership');
  assert.ok(flatMembership, 'flat destination gets membership-only facts, no container');
  assert.equal(findFact(facts, 'current-index-membership'), undefined);
  const oldTarget = findFact(facts, 'old-index-target-gone');
  assert.equal(oldTarget.legacyBareTarget, undefined, 'path mode never carries a legacy-bare fact');
  assert.equal(findFact(facts, 'title-container'), undefined, 'R12-F2: grouped->flat never carries a title fact');
});

test('manualMigrationChecklist: title-only change emits ONLY the orthogonal title fact, no path facts', () => {
  const p = profile();
  const old = entry({ group: 'admin', group_title: 'Old Title' });
  const next = entry({ group: 'admin', group_title: 'New Title' });
  const facts = manualMigrationChecklist(p, old, next);

  assert.deepEqual(facts, [
    { kind: 'title-container', containerTitle: 'New Title', oldContainerTitle: 'Old Title' },
  ]);
});

test('manualMigrationChecklist: grouped removal emits old-gone + no-live-sink + no-forbidden-wikilink facts', () => {
  const p = profile();
  const old = entry({ group: 'admin', group_title: 'Admin' });
  const facts = manualMigrationChecklist(p, old, null);

  assert.equal(findFact(facts, 'old-chapter-path-gone').path, 'vault/handbook/admin/items.md');
  assert.equal(findFact(facts, 'old-asset-dir-gone').path, 'vault/handbook/assets/admin/items');
  assert.equal(findFact(facts, 'old-index-target-gone').legacyBareTarget, undefined, 'path mode never carries a legacy-bare fact');
  const noSink = findFact(facts, 'no-live-capture-sink');
  assert.equal(noSink.oldDirQualified, 'vault/handbook/assets/admin/items');
  assert.equal(noSink.oldDirTail, 'admin/items');
  const noWikilink = findFact(facts, 'no-forbidden-wikilink');
  assert.equal(noWikilink.slug, 'items');
  assert.equal(noWikilink.oldChapterRelPath, 'vault/handbook/admin/items.md');
});

test('#294 manualMigrationChecklist: grouped removal in WIKILINK mode carries the vault-rel qualified target + legacyBareTarget', () => {
  const p = profile({ publish: { wikilinks: true } });
  const old = entry({ group: 'admin', group_title: 'Admin' });
  const facts = manualMigrationChecklist(p, old, null, 'handbook');

  const oldTarget = findFact(facts, 'old-index-target-gone');
  assert.equal(oldTarget.form, 'wikilink');
  assert.equal(oldTarget.expectedTarget, 'handbook/admin/items');
  assert.equal(oldTarget.legacyBareTarget, 'items');
});

test('manualMigrationChecklist: a flat removal (never a migration matter) => []', () => {
  assert.deepEqual(manualMigrationChecklist(profile(), entry(), null), []);
});

test('manualMigrationChecklist: a pure addition (no oldEntry) => []', () => {
  assert.deepEqual(manualMigrationChecklist(profile(), null, entry({ group: 'g', group_title: 'G' })), []);
});

test('manualMigrationChecklist: an untouched entry emits nothing', () => {
  const e = entry({ group: 'admin', group_title: 'Admin' });
  assert.deepEqual(manualMigrationChecklist(profile(), e, { ...e }), []);
});

test('R11-F3 combined same-entry fixture: group AND title both change => facts UNION path + title', () => {
  const p = profile();
  const old = entry({ group: 'admin', group_title: 'Admin' });
  const next = entry({ group: 'management', group_title: 'Ops' });
  const facts = manualMigrationChecklist(p, old, next);

  assert.ok(findFact(facts, 'current-chapter-path'));
  assert.ok(findFact(facts, 'current-asset-dir'));
  assert.ok(findFact(facts, 'old-chapter-path-gone'));
  const title = findFact(facts, 'title-container');
  assert.equal(title.containerTitle, 'Ops');
  assert.equal(title.oldContainerTitle, 'Admin');
});

test('§1b legacy-bare recognition: a bare [[users]] row is found via locateChapterLine, single vs duplicate', () => {
  // Reframed from the pre-1.8.0 "R14-F3 exactly-one exception" (now removed — see #294's Option A
  // formula, which makes old/new qualified targets always distinct). The underlying scan is still
  // exactly what the §1b union-scan legacy-bare check runs: locateChapterLine over the bare slug.
  const single = ['## Admin', '- [[users]]'];
  const result = locateChapterLine(single, 'users');
  assert.equal(result.present, true);
  assert.equal(result.multiple, false);
  assert.equal(result.matches.filter((m) => m.containerTitle === 'Admin').length, 1);

  const duplicated = ['## Admin', '- [[users]]', '- [[users]]'];
  assert.equal(locateChapterLine(duplicated, 'users').multiple, true, 'a second match halts as ambiguous');
});

test('R12-F5/R13-F2 stale-old-TOC-line fixture: the old target line is still present => stale (UNMET)', () => {
  const indexLines = [
    '- [Items](handbook/management/items.md)',
    '- [Items](handbook/admin/items.md)', // stale — should have been removed by the recipe
  ];
  assert.equal(locateChapterLine(indexLines, 'handbook/admin/items.md').present, true);
});

test('bare-wikilink old-target-gone: a [[slug]] line still under the OLD container => stale (UNMET)', () => {
  const indexLines = ['## Admin', '- [[items]]', '## Management'];
  const result = locateChapterLine(indexLines, 'items');
  assert.ok(result.matches.some((m) => m.containerTitle === 'Admin'), 'the stale line under Admin must be visible');
});

test('bare-wikilink grouped->flat: the flat [[slug]] line survives even though the string is identical (MET)', () => {
  // The required flat line sits BEFORE any heading (containerTitle: null) — the old-target-gone
  // fact only asks "no match under the OLD container", which this satisfies even though the
  // string is the same [[items]] the old grouped line also used.
  const indexLines = ['- [[items]]', '## Admin', '- [[other]]'];
  const result = locateChapterLine(indexLines, 'items');
  assert.equal(result.present, true, 'flat-membership fact is met');
  assert.equal(
    result.matches.filter((m) => m.containerTitle === 'Admin').length,
    0,
    'old-index-target-gone fact is also met — no match specifically under Admin',
  );
});

// =================================================================================================
// D6 — specReferencesDir (capture-spec red-flag predicate)
// =================================================================================================

test('specReferencesDir: a spec containing the OLD literal dir => flagged (stale-live-sink)', () => {
  const spec = `const OUTPUT_DIR = 'vault/handbook/assets/admin/orders';`;
  assert.equal(specReferencesDir(spec, 'vault/handbook/assets/admin/orders'), true);
});

test('specReferencesDir: prefix-collision — admin/orders-history is NOT flagged', () => {
  const spec = `const OUTPUT_DIR = 'admin/orders-history';`;
  assert.equal(specReferencesDir(spec, 'admin/orders'), false);
});

test('specReferencesDir: suffix-collision fixtures — hyphen/plus/non-ASCII predecessors are NOT boundaries', () => {
  assert.equal(specReferencesDir(`'legacy-admin/orders'`, 'admin/orders'), false);
  assert.equal(specReferencesDir(`'legacy+admin/orders'`, 'admin/orders'), false);
  assert.equal(specReferencesDir(`'éadmin/orders'`, 'admin/orders'), false);
});

test('specReferencesDir: longer-path fixture — a leading "/" is not a boundary', () => {
  assert.equal(specReferencesDir(`'screens/admin/orders'`, 'admin/orders'), false);
});

test('specReferencesDir: deliberate-miss fixture — a template-literal tail is NOT flagged (falls to confirmation)', () => {
  const spec = '`${OUT}/admin/orders`';
  assert.equal(specReferencesDir(spec, 'admin/orders'), false);
});

test('specReferencesDir: the output_dir-qualified spelling IS flagged', () => {
  const spec = `"docs/_attachments/admin/orders/capture.png"`;
  assert.equal(specReferencesDir(spec, 'docs/_attachments/admin/orders'), true);
});

test('specReferencesDir: a quoted helper-argument tail spelling IS flagged (two-sided boundary)', () => {
  const spec = `captureRegion(main, 'admin/orders/01.png')`;
  assert.equal(specReferencesDir(spec, 'admin/orders'), true);
});

test('specReferencesDir: no dir literal anywhere => never auto-passes (false), CONFIRMATION territory', () => {
  assert.equal(specReferencesDir(`const OUTPUT_DIR = chapterAssetDir(profile, entry);`, 'admin/orders'), false);
});

test('#256 boundary: needle at literal index 0 of specText exercises the before === null branch', () => {
  // Every fixture above has the dir literal preceded by other text (a quote, a template
  // interpolation, ...). Here the dir sits at the very start of specText, so `before` in
  // specReferencesDir must read null rather than indexing text[-1].
  assert.equal(specReferencesDir(`admin/orders/capture.png`, 'admin/orders'), true);
});

test('#256 boundary: needle at literal EOF of specText exercises the after === null branch', () => {
  // Mirror of the previous fixture: the dir literal ends exactly at the end of specText (an
  // unterminated string literal), so `after` must read null rather than indexing past the string.
  assert.equal(specReferencesDir(`captureRegion(main, 'admin/orders`, 'admin/orders'), true);
});

// =================================================================================================
// D6 — chapterHasWikilinkTo (forbidden-target predicate)
// =================================================================================================

const OLD_CHAPTER_REL_PATH = 'vault/handbook/admin/orders.md';

test('chapterHasWikilinkTo: unqualified forms that resolve to the removed slug are ALL forbidden', () => {
  const forbiddenTexts = [
    '[[orders]]',
    '[[orders|label]]',
    '[[orders#Refunds|refund workflow]]',
    '[[orders^blk]]',
    '[[orders.md]]',
    '[[orders.md#Refunds|refund workflow]]',
    '[[Orders]]',
  ];
  for (const text of forbiddenTexts) {
    assert.equal(
      chapterHasWikilinkTo(text, 'orders', OLD_CHAPTER_REL_PATH),
      true,
      `expected "${text}" to be forbidden`,
    );
  }
});

test('chapterHasWikilinkTo: a different note / different extension / no occurrence => permitted', () => {
  assert.equal(chapterHasWikilinkTo('[[other-note]]', 'orders', OLD_CHAPTER_REL_PATH), false);
  assert.equal(chapterHasWikilinkTo('[[orders.mdx]]', 'orders', OLD_CHAPTER_REL_PATH), false);
  assert.equal(chapterHasWikilinkTo('no wikilinks here at all', 'orders', OLD_CHAPTER_REL_PATH), false);
});

test('chapterHasWikilinkTo: slug pin [round-13 audit] — a genuinely different slug is consulted, not hardcoded to "orders"', () => {
  // Round-13 audit finding: every one of the ~24 chapterHasWikilinkTo calls in this file passes
  // the literal 'orders' for slug — this is the removal-safety predicate that gates whether a
  // manual-migration removal may proceed, so a mutant hardcoding `wantedSlug = 'orders'` inside
  // the function (ignoring the param) would silently break the check for every real removed
  // chapter except one whose slug happens to be 'orders', while every existing test stayed
  // green. Checked both directions so the slug's actual VALUE is what decides the result, not
  // just its presence: a wikilink to the real removed slug is forbidden; a wikilink to the OLD
  // constant 'orders' is NOT forbidden once we are checking for a different slug.
  const invoicesOldPath = 'vault/handbook/admin/invoices.md';
  assert.equal(
    chapterHasWikilinkTo('[[invoices]]', 'invoices', invoicesOldPath),
    true,
    "a wikilink to the removed chapter's OWN slug must be forbidden",
  );
  assert.equal(
    chapterHasWikilinkTo('[[orders]]', 'invoices', invoicesOldPath),
    false,
    "a wikilink to the UNRELATED 'orders' slug must not be forbidden when removing 'invoices'",
  );
});

test('chapterHasWikilinkTo: resolution-independent — a stale unqualified link is forbidden even if it resolves to a foreign note', () => {
  // The gate accepts any resolving wikilink; this predicate does not check resolution, only
  // target classification — a same-basename foreign note ("archive/orders.md") would make
  // [[orders]] resolve, but it is still a silent retarget and must be forbidden.
  assert.equal(chapterHasWikilinkTo('See [[orders]] for details.', 'orders', OLD_CHAPTER_REL_PATH), true);
});

test('chapterHasWikilinkTo: an explicit old-path-qualified link stays forbidden', () => {
  assert.equal(chapterHasWikilinkTo('[[admin/orders]]', 'orders', OLD_CHAPTER_REL_PATH), true);
  assert.equal(
    chapterHasWikilinkTo('[[handbook/admin/orders.md#Refunds]]', 'orders', OLD_CHAPTER_REL_PATH),
    true,
  );
});

test('chapterHasWikilinkTo: a differently-qualified explicit correction is PERMITTED', () => {
  assert.equal(chapterHasWikilinkTo('[[archive/orders]]', 'orders', OLD_CHAPTER_REL_PATH), false);
});

test('asymmetric-suffix backstop: a LONGER vault-rooted spelling of the removed path is PERMITTED here', () => {
  // isComponentSuffixMatch is asymmetric (target.length <= old.length only) — a longer,
  // vault-root-anchored spelling of the SAME removed path is deliberately NOT forbidden by this
  // predicate. It points at a file that no longer exists, so the separate handbook-wide
  // RESOLUTION scan catches it (broken link), not this fact — a backstopped miss. The reverse
  // (a symmetric match) would instead permanently deadlock the removal fact for any foreign,
  // still-kept note whose own path merely tail-contains the old path (e.g. a real note at
  // `x/handbook/admin/orders.md` — every qualified spelling of it would tail-align with the
  // shorter old path, so no "further-qualified spelling" could ever converge). False-forbid
  // (deadlock, no exit) is strictly worse than a miss (which has a backstop).
  // A separate (shorter) old path here so the vault-rooted target is genuinely LONGER —
  // OLD_CHAPTER_REL_PATH already starts with 'vault/', so it would not exercise this direction.
  assert.equal(chapterHasWikilinkTo('[[vault/handbook/admin/orders]]', 'orders', 'handbook/admin/orders.md'), false);
});

test('#256 boundary: a qualified target whose component length exactly equals oldChapterRelPath (offset === 0) is forbidden', () => {
  // isComponentSuffixMatch computes offset = old.length - target.length. The interior-suffix test
  // above (offset === 1, admin/orders inside vault/handbook/admin/orders) and the longer-than-old
  // backstop above (target.length > old.length, short-circuited before offset is even computed)
  // bracket this case without covering it: a target with exactly as many components as
  // OLD_CHAPTER_REL_PATH, so offset === 0 and the alignment starts at index 0 of `old`.
  assert.equal(
    chapterHasWikilinkTo('[[vault/handbook/admin/orders]]', 'orders', OLD_CHAPTER_REL_PATH),
    true,
  );
});

test('R18-F2 component-alignment pin: [[min/orders]] is permitted (not a raw string suffix match)', () => {
  // 'admin/orders' string-ends-with 'min/orders', but the path COMPONENTS do not align
  // ('admin' !== 'min') — a raw-string-suffix implementation would false-forbid this.
  assert.equal(chapterHasWikilinkTo('[[min/orders]]', 'orders', OLD_CHAPTER_REL_PATH), false);
});

test('R18-F2 qualified-equivalence pin: [[Admin/Orders.MD]] is forbidden (case-fold + .md-strip apply to qualified targets too)', () => {
  assert.equal(chapterHasWikilinkTo('[[Admin/Orders.MD]]', 'orders', OLD_CHAPTER_REL_PATH), true);
});

// Round-3 F3: non-rendered occurrences of the removed slug's wikilink syntax must never make this
// fact UNMET — a leftover documentation example would otherwise deadlock the removal forever.

test('R3-F3: an inline-code example is inert (permitted)', () => {
  const text = 'See the syntax `[[orders]]` for an example.';
  assert.equal(chapterHasWikilinkTo(text, 'orders', OLD_CHAPTER_REL_PATH), false);
});

test('R3-F3: a fenced code block is inert (permitted)', () => {
  const text = ['Example:', '```', '[[orders]]', '```', 'End.'].join('\n');
  assert.equal(chapterHasWikilinkTo(text, 'orders', OLD_CHAPTER_REL_PATH), false);
});

test('R3-F3: an HTML comment is inert (permitted)', () => {
  const text = 'Some text <!-- [[orders]] --> more text.';
  assert.equal(chapterHasWikilinkTo(text, 'orders', OLD_CHAPTER_REL_PATH), false);
});

test('R3-F3: a backslash-escaped link is inert (permitted)', () => {
  const text = 'This is escaped: \\[[orders]] and not a real link.';
  assert.equal(chapterHasWikilinkTo(text, 'orders', OLD_CHAPTER_REL_PATH), false);
});

test('R3-F3 control: a genuinely rendered, unfenced, unescaped link is still caught', () => {
  const text = 'Please see [[orders]] for the full workflow.';
  assert.equal(chapterHasWikilinkTo(text, 'orders', OLD_CHAPTER_REL_PATH), true);
});

test('R3-F3: a rendered link OUTSIDE a fenced block that also contains an inert example — the real one is still caught', () => {
  const text = [
    'The syntax looks like this:',
    '```',
    '[[orders]]',
    '```',
    'The actual reference is here: [[orders]].',
  ].join('\n');
  assert.equal(chapterHasWikilinkTo(text, 'orders', OLD_CHAPTER_REL_PATH), true);
});

// Round-4 F1: the chained-.replace() stripper (round 3) was unsound across INTERLEAVED contexts —
// backticks inside an HTML comment could pair with a LATER real fence in the separate fenced-code
// pass, erasing a genuinely rendered link sitting between them. The terminal fix is a single
// left-to-right pass (stripInertContexts) where whichever construct opens FIRST consumes to its
// own close before anything else is examined.

test('R4-F1: an HTML comment containing fence-like backticks does not pair with a LATER real fence (the rendered link between them is still caught)', () => {
  const text = ['<!-- ``` -->', '[[orders]]', '```', 'content', '```'].join('\n');
  assert.equal(chapterHasWikilinkTo(text, 'orders', OLD_CHAPTER_REL_PATH), true);
});

test('R4-F1: a four-backtick fence is inert (closing run must be >= the opening run length)', () => {
  const text = ['````', '[[orders]]', '````'].join('\n');
  assert.equal(chapterHasWikilinkTo(text, 'orders', OLD_CHAPTER_REL_PATH), false);
});

test('R4-F1: a multiline inline code span is inert (closing run must match the opening length exactly)', () => {
  const text = 'Here is `code spanning\n[[orders]] two lines` example.';
  assert.equal(chapterHasWikilinkTo(text, 'orders', OLD_CHAPTER_REL_PATH), false);
});

test('#254: a fence closes on a LONGER run than its opener (runLen >= openLen, not ===) — a later real link stays live', () => {
  // Every other fence fixture closes on an EQUAL-length run, so `>= openLen` was indistinguishable
  // from `=== openLen`. A 3-backtick fence closed by a 4-backtick run still closes (CommonMark's >=
  // rule), so the link on the line AFTER the close is genuinely rendered. A `=== openLen` mutant
  // would treat the fence as unterminated (running to EOF) and swallow that link.
  const text = ['```', '[[orders]]', '````', 'Real [[orders]] here.'].join('\n');
  assert.equal(chapterHasWikilinkTo(text, 'orders', OLD_CHAPTER_REL_PATH), true);
});

test('#254: an inline code span does NOT close on a LONGER run than its opener (runLen === openLen, not >=)', () => {
  // Every other inline-code fixture closes on an EQUAL-length run. A single-backtick span is NOT
  // closed by a later 2-backtick run (CommonMark's exact-length rule): the 2-run is content, and a
  // GENUINE later 1-backtick run closes the span — so `[[orders]]` sits INSIDE the span and is
  // inert. The closer is real, so `false` here does NOT rely on the unterminated-span-to-EOF path;
  // it's the exact-length rule alone. A `>= openLen` mutant would let the 2-backtick run close `x`
  // early, leaving `[[orders]]` outside the span and wrongly live.
  const text = 'Syntax `x`` [[orders]]` end.';
  assert.equal(chapterHasWikilinkTo(text, 'orders', OLD_CHAPTER_REL_PATH), false);
});

test('R4-F1: a triple-backtick run NOT at line start is an inline code span, not a fence — a LATER real link stays intact', () => {
  // Fences are recognized only at a line start; a mid-line ``` is an inline code span delimiter
  // instead, closing on the NEXT matching-length run rather than swallowing the rest of the
  // document as an "unterminated fence" would.
  const text = ['Inline example: ```[[orders]]``` end of span.', 'Real reference: [[orders]] here.'].join('\n');
  assert.equal(chapterHasWikilinkTo(text, 'orders', OLD_CHAPTER_REL_PATH), true);
});

// Round-6 F1: stripInertContexts must apply the SAME escape-SKIPPING duty as every other
// delimiter check — an escaped backtick with no real matching close anywhere in the text must
// never open an inline-code span that swallows everything after it to EOF.

test('R6-F1: an escaped backtick with no closer does not swallow a later heading+link (index probe)', () => {
  const indexLines = ['Type a literal \\`character.', '## Admin', '- [[items]]'];
  const result = locateChapterLine(indexLines, 'items');
  assert.equal(result.present, true, 'the escaped backtick must not hide the real TOC row');
  assert.equal(result.indexForm, 'headings', 'the real ## Admin heading must not be hidden either');
  assert.equal(result.containerTitle, 'Admin');
});

test('R6-F1: an escaped backtick with no closer does not swallow a later real chapter link', () => {
  const text = 'Type a literal \\`character.\nSee [[orders]] for details.';
  assert.equal(chapterHasWikilinkTo(text, 'orders', OLD_CHAPTER_REL_PATH), true);
});

// Round-7 F1: the escape guard checked isEscaped at ONE position only — after copying the escaped
// run's FIRST char, the scan reconsidered the REMAINING backticks/tildes of the SAME contiguous
// run as a fresh, unescaped opener. The escape must apply to the WHOLE run atomically. Both
// probes exercised through BOTH public entry points, as codex's exact repro did.

test('R7-F1: an escaped TWO-backtick run does not swallow later content (locateChapterLine)', () => {
  const indexLines = ['Type a literal \\`` character.', '## Admin', '- [[items]]'];
  const result = locateChapterLine(indexLines, 'items');
  assert.equal(result.present, true);
  assert.equal(result.indexForm, 'headings');
  assert.equal(result.containerTitle, 'Admin');
});

test('R7-F1: an escaped TWO-backtick run does not swallow a later real chapter link (chapterHasWikilinkTo)', () => {
  const text = 'Type a literal \\`` character.\nSee [[orders]] for details.';
  assert.equal(chapterHasWikilinkTo(text, 'orders', OLD_CHAPTER_REL_PATH), true);
});

test('R7-F1: an escaped THREE-backtick run does not swallow later content (locateChapterLine)', () => {
  const indexLines = ['Type a literal \\``` character.', '## Admin', '- [[items]]'];
  const result = locateChapterLine(indexLines, 'items');
  assert.equal(result.present, true);
  assert.equal(result.indexForm, 'headings');
  assert.equal(result.containerTitle, 'Admin');
});

test('R7-F1: an escaped THREE-backtick run does not swallow a later real chapter link (chapterHasWikilinkTo)', () => {
  const text = 'Type a literal \\``` character.\nSee [[orders]] for details.';
  assert.equal(chapterHasWikilinkTo(text, 'orders', OLD_CHAPTER_REL_PATH), true);
});

// =================================================================================================
// D6 — renderManualMigrationHalt / context-free reconstruction
// =================================================================================================

test('R10-F5/R11-F4 halt-record pin: the rendered halt names every changed entry, incl. the old container title', () => {
  const p = profile();
  const changeA = {
    kind: 'group-change',
    slug: 'a',
    oldEntry: entry({ slug: 'a', group: 'g1', group_title: 'G1' }),
    newEntry: entry({ slug: 'a', group: 'g1x', group_title: 'G1' }),
  };
  const changeB = {
    kind: 'removal',
    slug: 'b',
    oldEntry: entry({ slug: 'b', group: 'g2', group_title: 'G2' }),
    newEntry: null,
  };
  const changes = [changeA, changeB];
  const checklists = changes.map((c) => manualMigrationChecklist(p, c.oldEntry, c.newEntry));
  const text = renderManualMigrationHalt(changes, checklists);

  assert.match(text, /^This manifest change requires manual group migration \(not automated in 1\.5\.0\):/);
  assert.match(text, /a: vault\/handbook\/g1\/a\.md -> vault\/handbook\/g1x\/a\.md/);
  assert.match(text, /was under container 'G1'/);
  assert.match(text, /b: removed — delete vault\/handbook\/g2\/b\.md.*was under container 'G2'/);
  assert.match(text, /Follow the manual migration recipe in references\/revalidation\.md, then re-run\.$/);
});

function extractOldContainerTitle(line) {
  const m = line.match(/was under container '([^']+)'/);
  return m ? m[1] : null;
}

test('context-free reconstruction (a): a grouped removal record supplies the old title with no delta object', () => {
  const p = profile();
  const old = entry({ slug: 'orders', group: 'admin', group_title: 'Admin' });
  const change = { kind: 'removal', slug: 'orders', oldEntry: old, newEntry: null };
  const facts = manualMigrationChecklist(p, old, null);
  const text = renderManualMigrationHalt([change], [facts]);

  const line = text.split('\n').find((l) => l.includes('orders:'));
  const reconstructedTitle = extractOldContainerTitle(line);
  assert.equal(reconstructedTitle, 'Admin');

  // The old-container proof is runnable from the reconstructed title alone.
  const indexLines = [`## ${reconstructedTitle}`, '- [[orders]]'];
  assert.ok(locateChapterLine(indexLines, 'orders').matches.some((m) => m.containerTitle === reconstructedTitle));
});

test('context-free reconstruction (b): a grouped->flat move record is the ONLY source of the old title', () => {
  const p = profile();
  const old = entry({ slug: 'orders', group: 'admin', group_title: 'Admin' });
  const next = entry({ slug: 'orders' }); // flat — carries no group_title at all
  assert.equal(next.group_title, undefined, 'the current entry has no title to fall back on');

  const change = { kind: 'group-change', slug: 'orders', oldEntry: old, newEntry: next };
  const facts = manualMigrationChecklist(p, old, next);
  const text = renderManualMigrationHalt([change], [facts]);

  const line = text.split('\n').find((l) => l.includes('orders:'));
  assert.equal(extractOldContainerTitle(line), 'Admin');
});

test('context-free reconstruction (c): the scan-failure re-embed preserves the old-container suffix verbatim', () => {
  const p = profile();
  const old = entry({ slug: 'orders', group: 'admin', group_title: 'Admin' });
  const next = entry({ slug: 'orders' });
  const change = { kind: 'group-change', slug: 'orders', oldEntry: old, newEntry: next };
  const facts = manualMigrationChecklist(p, old, next);

  const scanFailures = [{ chapter: 'other.md', line: 12, target: 'admin/orders.md' }];
  const text = renderManualMigrationHalt([change], [facts], scanFailures);

  assert.match(text, /^Post-migration link scan failed \(1 broken\):/);
  const line = text.split('\n').find((l) => l.includes('orders:'));
  assert.equal(extractOldContainerTitle(line), 'Admin', 'the re-embed must preserve the old-title suffix');
  assert.match(
    text,
    /re-verify the terminal facts above, repeat the handbook-wide link scan, and re-run the touched-chapter gates, in that order/,
  );
});

test('#255: renderManualMigrationHalt scan-failure header + detail cover ALL tuples, not just the first', () => {
  // The only non-empty scanFailures fixture has exactly one tuple, so a mutant hardcoding `(1
  // broken)` or reading scanFailures[0] only would survive. Two distinct tuples pin the real length
  // in the header AND both `chapter:line -> target` details in the joined body.
  const p = profile();
  const old = entry({ slug: 'orders', group: 'admin', group_title: 'Admin' });
  const next = entry({ slug: 'orders' });
  const change = { kind: 'group-change', slug: 'orders', oldEntry: old, newEntry: next };
  const facts = manualMigrationChecklist(p, old, next);

  const scanFailures = [
    { chapter: 'a.md', line: 3, target: 'admin/orders.md' },
    { chapter: 'b.md', line: 9, target: 'admin/items.md' },
  ];
  const text = renderManualMigrationHalt([change], [facts], scanFailures);
  assert.match(text, /^Post-migration link scan failed \(2 broken\):/);
  assert.ok(text.includes('a.md:3 -> admin/orders.md'));
  assert.ok(text.includes('b.md:9 -> admin/items.md'));
});

test('renderManualMigrationHalt: an EMPTY scanFailures array uses the normal format, not the scan-failure format [round-13 audit]', () => {
  // Round-13 audit finding: every existing call either OMITS scanFailures (undefined) or passes
  // a non-empty array — the `.length > 0` half of `if (scanFailures && scanFailures.length > 0)`
  // is never independently exercised. A caller that runs the post-migration scan and finds
  // nothing may legitimately pass `[]` (truthy, but empty) rather than omitting the argument. A
  // mutant simplifying the guard to `if (scanFailures)` would treat that as "has failures" and
  // render the wrong (scan-failed) format for a clean migration.
  const p = profile();
  const old = entry({ slug: 'orders', group: 'admin', group_title: 'Admin' });
  const next = entry({ slug: 'orders' });
  const change = { kind: 'group-change', slug: 'orders', oldEntry: old, newEntry: next };
  const facts = manualMigrationChecklist(p, old, next);

  const text = renderManualMigrationHalt([change], [facts], []);
  assert.match(text, /^This manifest change requires manual group migration/);
  assert.ok(!text.startsWith('Post-migration link scan failed'), 'an empty scanFailures array must not trigger the scan-failure format');
});

test('R10-F4 mixed-domain fixture: a retained change + a grouped removal + a new-only addition (no early return)', () => {
  const old = [entry({ slug: 'a', group: 'g1', group_title: 'G1' }), entry({ slug: 'b', group: 'g2', group_title: 'G2' })];
  const next = [entry({ slug: 'a', group: 'g1x', group_title: 'G1' }), entry({ slug: 'c', group: 'g3', group_title: 'G3' })];
  const { changes } = groupChanges(old, next);

  assert.equal(changes.length, 2);
  assert.equal(changes[0].kind, 'group-change');
  assert.equal(changes[0].slug, 'a');
  assert.equal(changes[1].kind, 'removal');
  assert.equal(changes[1].slug, 'b');

  const p = profile();
  const factsA = manualMigrationChecklist(p, changes[0].oldEntry, changes[0].newEntry);
  const factsB = manualMigrationChecklist(p, changes[1].oldEntry, changes[1].newEntry);
  assert.ok(factsA.length > 0);
  assert.ok(factsB.length > 0);
});

// =================================================================================================
// Consumer-binding STRUCTURAL pin — capture.example.spec.ts
// =================================================================================================

// ONE separator atom, ECMAScript-complete line-terminator set (LF, CR/CRLF, U+2028, U+2029). The
// comment branch consumes THROUGH its mandatory terminator (no bare `[^\n]*`) so pattern tokens
// can never match inside comment text; identifiers are atomic so this is the only de-sync
// position. See chapter-paths.mjs's own header banner is not the source of truth here — this pin
// deliberately lives in the test file (plan §5), not the lib, since it inspects a DIFFERENT file.
const SEP = '(?:\\s|\\/\\/[^\\n\\r\\u2028\\u2029]*(?:\\r\\n?|\\n|\\u2028|\\u2029))';
const S_STAR = `${SEP}*`;
const S_PLUS = `${SEP}+`;

function bindingAnchorSource() {
  return `^[ \\t]*const${S_PLUS}OUTPUT_DIR${S_STAR}=`;
}
function bindingRhsSource() {
  return `^[ \\t]*const${S_PLUS}OUTPUT_DIR${S_STAR}=${S_STAR}chapterAssetDir${S_STAR}\\(`;
}
// F2: String.match() WITHOUT the /g flag returns at most ONE result (the first match), so
// (text.match(re) || []).length was ALWAYS 0 or 1 regardless of how many times the pattern
// actually occurs — a non-global bindingAnchor/bindingRhs regex made assertion (i)/(ii)'s
// "exactly one" check false-green whenever a SECOND match existed anywhere earlier in the text
// (codex reproduced: a helper-looking decoy + a non-helper real binding both "counted" as 1,
// since .match() just returns the FIRST hit and never notices there's a second). Always count via
// matchAll on a forced-global regex, preserving every other flag (e.g. 'm').
function countMatches(re, text) {
  const global = re.global ? re : new RegExp(re.source, `${re.flags}g`);
  return [...text.matchAll(global)].length;
}
function isLineCommentedAtStart(text, index) {
  const lineStart = text.lastIndexOf('\n', index - 1) + 1;
  const prefix = text.slice(lineStart, index);
  return /^\s*\/\//.test(prefix);
}
function nonCommentedMatchCount(source, flags, text) {
  const re = new RegExp(source, flags.includes('g') ? flags : `${flags}g`);
  let m;
  let count = 0;
  while ((m = re.exec(text))) {
    if (!isLineCommentedAtStart(text, m.index)) count += 1;
  }
  return count;
}

test('F2: countMatches itself is pinned — a genuine two-binding sample counts 2, not 1 (closes the non-global false-green)', () => {
  const twoBindings = [
    'const OUTPUT_DIR = chapterAssetDir(profile, entry);',
    'const OUTPUT_DIR = chapterAssetDir(profile, entry);',
  ].join('\n');
  assert.equal(countMatches(new RegExp(bindingAnchorSource(), 'm'), twoBindings), 2);
  assert.equal(countMatches(new RegExp(bindingRhsSource(), 'm'), twoBindings), 2);
});

test('F2 mutation (cc): a decoy inside a multiline template literal plus a non-helper real binding is caught', () => {
  // The decoy's TEMPLATE LITERAL spans multiple lines, so its interior line
  // "const OUTPUT_DIR = chapterAssetDir(profile, entry);" sits at a genuine LINE START (right
  // after the template literal's opening line) — a real second match for the ^-anchored pattern,
  // not merely text embedded mid-line (which would never match the anchor regardless of the
  // counting bug). The REAL binding's RHS is a non-helper array-join. Under the old non-global
  // countMatches, `.match()` would return just the FIRST match (the decoy's) for BOTH assertions
  // (i) and (ii) and silently report count=1 for each — false-green, never seeing that a) there
  // are really two anchor matches and b) the real binding's RHS is broken.
  const text = [
    'const DECOY = `',
    'const OUTPUT_DIR = chapterAssetDir(profile, entry);',
    '`;',
    'const  OUTPUT_DIR  = ["handbook/assets/items"].join("");',
  ].join('\n');
  assert.equal(countMatches(new RegExp(bindingAnchorSource(), 'm'), text), 2, 'two genuine anchor matches must be counted');
  // The RHS pin only counts matches whose RHS actually IS chapterAssetDir( — the decoy's embedded
  // line satisfies it, the real (non-helper) line does not, so this stays 1, not 2 — the mismatch
  // between (i)=2 and (ii)=1 is itself the signal a decoy is present alongside a broken real RHS.
  assert.equal(countMatches(new RegExp(bindingRhsSource(), 'm'), text), 1);
});

test('consumer-binding structural pin: capture.example.spec.ts', () => {
  const text = readFileSync(SPEC_PATH, 'utf8');
  const N = 4;

  // (0) comment-model guard — fail-closed on any /* byte pair anywhere in the file.
  assert.equal(text.includes('/*'), false, 'the skeleton must contain no block-comment byte pair');

  // (i) exactly one whole-text binding anchor match.
  assert.equal(countMatches(new RegExp(bindingAnchorSource(), 'm'), text), 1);

  // (ii) the sole binding's RHS is the chapterAssetDir( call.
  assert.equal(countMatches(new RegExp(bindingRhsSource(), 'm'), text), 1);

  // (iii) sink-interpolation pin: N occurrences of the ${OUTPUT_DIR}/ sink spelling.
  assert.equal(countMatches(/\$\{OUTPUT_DIR\}\//g, text), N);

  // (iv) call-site pin: N non-commented captureRegion(Clipped)? call sites.
  assert.equal(nonCommentedMatchCount(`\\bcaptureRegion(?:Clipped)?${S_STAR}\\(`, 'g', text), N);

  // (v) artifact pin: N .png occurrences.
  assert.equal(countMatches(/\.png/g, text), N);

  // (vi) raw-idiom ban: zero non-commented screenshot/toHaveScreenshot idioms.
  assert.equal(nonCommentedMatchCount(`\\.${S_STAR}(?:screenshot|toHaveScreenshot)${S_STAR}\\(`, 'g', text), 0);
});

test('green-case binding tolerance: whitespace- and comment-separated bindings still match at count=1', () => {
  const snippets = [
    "const OUTPUT_DIR = chapterAssetDir(profile, entry);",
    "const  OUTPUT_DIR  =  chapterAssetDir(profile, entry);",
    "const OUTPUT_DIR=chapterAssetDir(profile, entry);",
    "const // note\nOUTPUT_DIR = chapterAssetDir(profile, entry);",
    "const OUTPUT_DIR = // note\nchapterAssetDir(profile, entry);",
  ];
  for (const snippet of snippets) {
    assert.equal(countMatches(new RegExp(bindingAnchorSource(), 'm'), snippet), 1, snippet);
    assert.equal(countMatches(new RegExp(bindingRhsSource(), 'm'), snippet), 1, snippet);
  }
});

test('green-case binding tolerance: CR/U+2028/U+2029-terminated comments after const do not false-halt', () => {
  const variants = [
    `const // note\rOUTPUT_DIR = chapterAssetDir(profile, entry);`,
    `const // note${'\u2028'}OUTPUT_DIR = chapterAssetDir(profile, entry);`,
    `const // note${'\u2029'}OUTPUT_DIR = chapterAssetDir(profile, entry);`,
  ];
  for (const snippet of variants) {
    assert.equal(countMatches(new RegExp(bindingAnchorSource(), 'm'), snippet), 1, JSON.stringify(snippet));
    assert.equal(countMatches(new RegExp(bindingRhsSource(), 'm'), snippet), 1, JSON.stringify(snippet));
  }
});

test('keyword-fusion guard: constOUTPUT_DIR (fused identifier, no declaration) does NOT match', () => {
  const snippet = 'constOUTPUT_DIR = chapterAssetDir(profile, entry);';
  assert.equal(countMatches(new RegExp(bindingAnchorSource(), 'm'), snippet), 0);
});

test('decoy resistance: a commented-out real binding + a non-literal RHS decoy does not satisfy the pin', () => {
  const snippet = [
    "// const OUTPUT_DIR = chapterAssetDir(profile, entry);",
    "const OUTPUT_DIR = ['handbook/assets/items'].join('');",
  ].join('\n');
  // Anchor (i) matches only the REAL (non-commented) line — the commented decoy line does not
  // start with "const" at column 0 net of the "// " prefix under the anchor's own [ \t]* class
  // (which does not include '/'), so it is not a second anchor match.
  assert.equal(countMatches(new RegExp(bindingAnchorSource(), 'm'), snippet), 1);
  // But the real line's RHS is not chapterAssetDir(, so the RHS pin correctly fails.
  assert.equal(countMatches(new RegExp(bindingRhsSource(), 'm'), snippet), 0);
});

// =================================================================================================
// [1.11.0] THE RE-READ POSTCONDITION. Until this release the writer emitted whatever the caller's
// chapterLink and group_title produced — a plain-label check on the container label, none at all on
// the child row. A manifest value that is legal everywhere upstream therefore got written into a
// clean file, and THIS SAME SCANNER refused that file on every later run: nested-list automation
// died for every chapter and every group in it, permanently, from an index the tool itself wrote.
//
// The writer now re-reads the bytes it is about to hand back through its own reader and, if that
// reader refuses them, writes NOTHING and returns {kind:'unwritable', field}. `field` names the
// manifest value at fault, derived by substituting a known-good stand-in for one emitted line at a
// time — not by inspecting the value — so it stays right for causes nobody has enumerated.
//
// The tables below were the six causes' defect pins; they are now the refusal's coverage. Every cell
// was re-measured against the real module after the fix. The marker CONTROLS are the load-bearing
// part: a postcondition that refused everything would also make these pass, so each group_title is
// asserted to still WRITE on the markers where it was always harmless. The refusal is exactly as
// marker-scoped as the defect was.
// =================================================================================================

const UNWRITABLE_TITLES = [
  { title: 'Use `--force`', why: 'inline code' },
  { title: 'Old <!-- x --> notes', why: 'HTML comment' },
  // NOT a fence, despite how it reads. Measured: a title's backtick run blanks only up to the
  // matching run on the NEXT line (` tail` survives), which is the code-SPAN rule; a real fence
  // needs the run at LINE START and swallows to EOF. A title can never be at line start — the row
  // is always `<indent><marker> [<title>](<target>)`. Same mechanism as the single-backtick row
  // above, kept as its own case only because the triple run is what an operator recognizes.
  { title: 'Code ``` here', why: 'a triple backtick run (still a code span, not a fence)' },
  // Written as escapes on purpose: a literal here would be invisible in this source too, which is
  // exactly why these two were the last of the six to be found.
  { title: `Plans${String.fromCodePoint(0x2028)}Beta`, why: 'U+2028 LINE SEPARATOR' },
  { title: `Plans${String.fromCodePoint(0x2029)}Beta`, why: 'U+2029 PARAGRAPH SEPARATOR' },
];

// Each value is refused on the markers where it used to poison the file and still WRITES on the
// others. Both halves are asserted; the second is what proves the postcondition is not a blunt
// refuse-everything.
const UNWRITABLE_GROUP_TITLES = [
  { groupTitle: 'Sales/Marketing', refusedOn: ['*', '+'], writesOn: ['-'], why: 'bare-path bullet (contains /)' },
  { groupTitle: 'billing.md', refusedOn: ['*', '+'], writesOn: ['-'], why: 'bare-path bullet (ends .md)' },
  { groupTitle: 'FAQ: basics', refusedOn: ['-'], writesOn: ['*', '+'], why: 'reads back as an MkDocs `- key: value` nav row' },
  { groupTitle: 'Admin:', refusedOn: ['-'], writesOn: ['*', '+'], why: 'reads back as an MkDocs nav row' },
  { groupTitle: '---', refusedOn: ['-'], writesOn: ['*', '+'], why: 'reads back as a thematic break' },
];

for (const { title, why } of UNWRITABLE_TITLES) {
  test(`re-read postcondition [1.11.0]: a title carrying ${why} is refused outright, on every marker, and nothing is written`, () => {
    for (const marker of ['-', '*', '+']) {
      const seed = ['# Summary', '', `${marker} Guides`, `  ${marker} [G](g.md)`, ''];
      const before = seed.slice();
      const result = wireNestedListChapter(seed, 'Guides', `[${title}](admin/plans.md)`);

      assert.equal(result.kind, 'unwritable', `${marker}: refused rather than written`);
      assert.equal(result.field, 'title', `${marker}: the chapter's own title is named as the culprit`);
      assert.ok(!('newLines' in result), `${marker}: an unwritable outcome carries no index to persist`);
      // The whole point: the caller has nothing it could persist, so the file cannot be poisoned.
      assert.deepEqual(seed, before, `${marker}: the input array is untouched`);
    }
  });
}

for (const { groupTitle, refusedOn, writesOn, why } of UNWRITABLE_GROUP_TITLES) {
  test(`re-read postcondition [1.11.0]: group_title ${JSON.stringify(groupTitle)} is refused on ${refusedOn.join('/')} (${why}) and still writes on ${writesOn.join('/')}`, () => {
    const chapterLink = '[Items](admin/items.md)';
    // Both allowlists accept it — that is what made this reachable, and it is unchanged by the fix.
    assert.equal(isPlainLabel(groupTitle), true, 'the plain-label allowlist still accepts it');
    assert.deepEqual(validateGroups([{ slug: 'q1', group: 'g', group_title: groupTitle }]), [], 'and so does validateGroups');

    for (const marker of refusedOn) {
      const seed = ['# Summary', '', `${marker} Other`, `  ${marker} [G](g.md)`, ''];
      const result = wireNestedListChapter(seed, groupTitle, chapterLink);
      assert.equal(result.kind, 'unwritable', `${marker}: refused`);
      assert.equal(result.field, 'group_title', `${marker}: the group_title is named, not the chapter title`);
      assert.ok(!('newLines' in result), `${marker}: nothing to persist`);
    }

    // CONTROL — the same value on a marker where it never poisoned anything must still write. This
    // is what distinguishes a scoped postcondition from a refuse-everything one, and without it
    // every assertion above would also pass against a writer that had simply stopped working.
    for (const marker of writesOn) {
      const seed = ['# Summary', '', `${marker} Other`, `  ${marker} [G](g.md)`, ''];
      const result = wireNestedListChapter(seed, groupTitle, chapterLink);
      assert.equal(result.kind, 'inserted', `${marker} control: still writes`);
      assert.equal(result.created, true, `${marker} control: creates the container`);
    }
  });
}

test('re-read postcondition [1.11.0]: U+2028/U+2029 in a group_title is refused on EVERY marker — the one container cause with no safe marker', () => {
  // Deliberately not a row in UNWRITABLE_GROUP_TITLES: every row there carries a `writesOn` control,
  // and this value has none. Keeping it out of the table rather than inventing an empty control is
  // the point — the table's framing ("harmless on one marker, fatal on another") is true of its five
  // rows and is NOT a general property, and an unpinned exception is how that framing would have
  // quietly become a rule. The title half is covered by UNWRITABLE_TITLES; this is the container half.
  for (const sep of [0x2028, 0x2029]) {
    const groupTitle = `Sales${String.fromCodePoint(sep)}Ops`;
    assert.equal(isPlainLabel(groupTitle), true, 'the plain-label allowlist accepts it');
    for (const marker of ['-', '*', '+']) {
      const seed = ['# Summary', '', `${marker} Other`, `  ${marker} [G](g.md)`, ''];
      const result = wireNestedListChapter(seed, groupTitle, '[Items](admin/items.md)');
      assert.equal(result.kind, 'unwritable', `U+${sep.toString(16)} / ${marker}: refused`);
      assert.equal(result.field, 'group_title');
    }
  }
});

test('re-read postcondition [1.11.0]: which marker decides is a property of the EMITTED line, not of the file', () => {
  // Every other fixture in this section uses a homogeneous-marker index, so all of them would still
  // pass if the rule really were "a `-` index" vs "a `*` index" — which is how both adapters phrase
  // it. It is not. The ZERO-create branch emits `firstTopMarker`: the marker of the FIRST indent-0
  // bullet in the file. Measured, and it inverts cleanly when the first bullet flips:
  const mostlyStar = ['- [Introduction](README.md)', '* Admin', '  * [Items](admin/items.md)', ''];
  const mostlyDash = ['* [Introduction](README.md)', '- Admin', '  - [Items](admin/items.md)', ''];
  const link = '[Plans](admin/plans.md)';

  // A bare-path-shaped group_title is refused on `*`/`+` — but this file emits `-`, so it is written.
  const a = wireNestedListChapter(mostlyStar, 'Sales/Marketing', link);
  assert.equal(a.kind, 'inserted', 'first bullet is `-`, so the emitted container is `- Sales/Marketing`');
  assert.ok(a.newLines.includes('- Sales/Marketing'));

  // The same value on a mostly-`-` file whose FIRST bullet is `*` is refused.
  assert.deepEqual(
    wireNestedListChapter(mostlyDash, 'Sales/Marketing', link),
    { kind: 'unwritable', field: 'group_title' },
  );

  // And the hyphen-run cause inverts the other way, for the same reason.
  assert.deepEqual(
    wireNestedListChapter(mostlyStar, '---', link),
    { kind: 'unwritable', field: 'group_title' },
  );
  assert.equal(wireNestedListChapter(mostlyDash, '---', link).kind, 'inserted');
});

test('re-read postcondition [1.11.0]: on a `*`/`+` index the refusal follows the target\'s GROUP PREFIX, not the broken title', () => {
  // The adapters explain the `*`/`+` case by saying a target-breaking title triggers isBarePathBullet.
  // That is true of what the adapters EMIT and false as a property of this writer: the `/` does the
  // work. A grouped chapter's target always carries its group prefix (chapterRelPath returns
  // `<group>/<slug>.md` whenever `group` is set, and this writer is reached only for grouped
  // entries), so the raw fallback looks like a bare path. With a slash-free target the identical
  // title is written and converges normally — pinned so the attribution cannot drift back.
  const title = 'Plans [Beta]';
  for (const marker of ['*', '+']) {
    const seed = [`${marker} Guides`, `  ${marker} [G](g.md)`, ''];

    const grouped = wireNestedListChapter(seed, 'Guides', `[${title}](admin/plans.md)`);
    assert.equal(grouped.kind, 'unwritable', `${marker}: a group-prefixed target is refused`);
    assert.equal(grouped.field, 'title');

    const flat = wireNestedListChapter(seed, 'Guides', `[${title}](plans.md)`);
    assert.equal(flat.kind, 'inserted', `${marker}: the SAME title with a slash-free target writes`);
    assert.equal(
      wireNestedListChapter(flat.newLines, 'Guides', `[${title}](plans.md)`).kind,
      'present',
      `${marker}: and converges on the next run, so the slash is the variable`,
    );
  }
});

test('re-read postcondition [1.11.0]: the writer never emits a chapter row at indent 0, so a left-margin row is always operator-typed', () => {
  // Earlier prose in both adapters described a "broken row at the left margin" as something the
  // WRITER's own insert could land in. It cannot: every child row is emitted at `childIndent`, which
  // containerOwnerScan caps to 2..4 and defaults to 2, and the only indent-0 line the writer ever
  // emits is a container whose label passed isPlainLabel first. Pinned because the retired prose was
  // wrong about the agent, not about the shape — and a future reader could reintroduce it.
  const cases = [
    { seed: ['- Admin', '  - [G](g.md)', ''], why: 'existing 2-space child' },
    { seed: ['- Admin', '    - [G](g.md)', ''], why: 'existing 4-space child' },
    { seed: ['- Admin', ''], why: 'container with no child' },
    { seed: ['- Other', '  - [G](g.md)', ''], why: 'ZERO-create branch' },
  ];
  for (const { seed, why } of cases) {
    const result = wireNestedListChapter(seed, 'Admin', '[Items](admin/items.md)');
    assert.equal(result.kind, 'inserted', why);
    const row = result.newLines.find((line) => line.includes('admin/items.md'));
    assert.ok(row.length - row.trimStart().length >= 2, `${why}: the emitted row is never at indent 0`);
  }

  // The SCENARIO is still real, and still operator-reachable: a hand-typed target-breaking row at the
  // left margin fails the indent-0 plain-label gate and takes the whole file with it. 1.11.0 does not
  // change that — the writer's refusal is the correct response to an already-unreadable file, not the
  // cause of it.
  const handTyped = ['# Summary', '', '- [Plans [Beta]](admin/plans.md)', '- Admin', '  - [G](g.md)', ''];
  assert.equal(wireNestedListChapter(handTyped, 'Admin', '[Items](admin/items.md)').kind, 'not-a-list');
});

test('re-read postcondition [1.11.0]: a clean manifest is unaffected — the fix must not cost a legitimate write', () => {
  // The counterfactual that matters most operationally. Every ordinary shape still goes through.
  const cases = [
    { seed: ['- Admin', '  - [Overview](admin/overview.md)'], group: 'Admin', link: '[Items](admin/items.md)', created: false },
    { seed: ['- Other', '  - [G](g.md)'], group: 'Admin', link: '[Items](admin/items.md)', created: true },
    { seed: ['* Admin', '  * [Overview](admin/overview.md)'], group: 'Admin', link: '[Items](admin/items.md)', created: false },
    { seed: ['- Admin', '  - [[admin/overview|Overview]]'], group: 'Admin', link: '[[admin/items|Items]]', created: false },
    { seed: ['- Admin', '    - [Overview](admin/overview.md)'], group: 'Admin', link: '[Items](admin/items.md)', created: false },
  ];
  for (const { seed, group, link, created } of cases) {
    const result = wireNestedListChapter(seed, group, link);
    assert.equal(result.kind, 'inserted', `${JSON.stringify(seed)} + ${link}`);
    assert.equal(result.created, created);
    assert.ok(result.newLines.some((line) => line.includes(link)), 'the row is really there');
  }
});

// Still unfixed after the re-read postcondition: that check asks "can I read back what I am about
// to write", and every generation of an edited title IS readable. Measured against the fix, this
// test stays green while all twelve lockout pins went red — which is the map worth having: the two
// defects looked like one and are not.
const PINNED_DEFECT_ACCUMULATION =
  'PINNED DEFECT [1.11.0] (RED HERE MEANS FIXED — delete this test and retire the accumulation '
  + 'disclosure in BOTH adapters)';

test(`${PINNED_DEFECT_ACCUMULATION}: a chapter title EDITED between publishes accumulates one dead row per edit, without bound`, () => {
  // The adapters measured "every placement x title-resolvability combination" with the manifest held
  // FIXED, and concluded no combination grows without bound. Title EDITS are a third axis. Each edit
  // produces a different link string, so the membership guard correctly sees a different row and
  // inserts — the bound is the number of edits, which is not a bound.
  const target = 'admin/plans.md';
  let lines = ['- Admin', '  - [Overview](admin/overview.md)', ''];
  let edit = 0;
  // Record what every run RETURNS, not just what it writes. The row count alone cannot tell a
  // `present` halt apart from any other non-`inserted` outcome, because only `inserted` mutates
  // `lines` — so a module that returned `unwritable` on all 15 non-inserting runs would leave
  // exactly the same 5 rows and keep this test green. Both adapters describe these 15 runs in
  // prose, so the distribution is part of what this test pins.
  const outcomes = [];
  for (let run = 1; run <= 20; run += 1) {
    if (run % 4 === 1 && run > 1) edit += 1;
    const chapterLink = `[Plans [Beta ${edit}]](${target})`; // target-breaking in every generation
    if (locateChapterLine(lines, target, { wikilink: false }).present) { outcomes.push('step0-present'); continue; }
    const written = wireNestedListChapter(lines, 'Admin', chapterLink);
    outcomes.push(written.kind);
    if (written.kind === 'inserted') lines = written.newLines;
  }
  const rows = lines.filter((line) => line.includes(target));
  assert.equal(
    rows.length,
    5,
    'one dead row per title edit survives — if this count drops, the accumulation is FIXED: delete this test and retire the adapter prose',
  );
  const tally = outcomes.reduce((acc, kind) => ({ ...acc, [kind]: (acc[kind] ?? 0) + 1 }), {});
  assert.deepEqual(
    tally,
    { inserted: 5, present: 15 },
    'the 15 non-inserting runs each raise the `present` halt — the adapters say so in prose; any other '
      + 'distribution means the prose is stale even though the row count still reads 5',
  );
  // Every generation is still there, none replaced: this is accumulation, not churn.
  for (let generation = 0; generation <= 4; generation += 1) {
    assert.ok(
      rows.some((row) => row.includes(`Plans [Beta ${generation}]`)),
      `generation ${generation} is still in the index`,
    );
  }
});
