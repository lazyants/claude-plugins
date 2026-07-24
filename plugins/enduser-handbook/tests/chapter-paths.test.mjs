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
  locateChapterLine,
  currentIndexExpectedTarget,
  classifyChapterWiring,
  findContainer,
  wireNestedListChapter,
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
// container-anchoring check is `heading[1].length >= 2` (chapter-paths.mjs:814,:875), so nothing
// in this file distinguishes it from a narrower `=== 2`. No fixture anywhere uses a depth-3 (###)
// heading. Left unpinned on purpose: the module's own docstring (chapter-paths.mjs:864-865, D6
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
  // Round-18 finding: `validateGroups`'s grouped branch calls `duplicateSlugHalts(entries, ...)`
  // on the FULL entry list (chapter-paths.mjs:373) — every existing duplicate-in-a-grouped-
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
