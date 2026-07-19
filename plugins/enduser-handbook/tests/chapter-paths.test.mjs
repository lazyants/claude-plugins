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
  findContainer,
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

test('F1: findContainer classifies a mkdocs.yml-shaped YAML comment as non-heading (manual-wiring), never headings-form', () => {
  // A single '#'-prefixed comment line, exactly as a real mkdocs.yml nav: block would carry —
  // must not be mistaken for evidence of a Markdown headings-form index.
  const indexLines = ['# Main navigation', 'nav:', '  - Home: index.md', '  - Admin: admin/index.md'];
  assert.deepEqual(findContainer(indexLines, 'Admin'), { kind: 'non-heading' });
});

test('F1: findContainer classifies a GitBook "# Summary" + nested-list file as non-heading, never headings-form', () => {
  // A GitBook SUMMARY.md: one H1 document title, then nested bullet lists — no real '##' group
  // containers anywhere, so this must stay manual-wiring territory, not silently automated.
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
    `Duplicate chapter slug 'x' — chapter slugs must be globally unique across all groups (wikilinks and Quartz-shortest resolution key on the basename).`,
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
    `Group 'g' carries conflicting group_title values ('Alpha' vs 'Beta') — align all entries of the group.`,
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

test('#221: a clean group-free manifest still returns []', () => {
  const halts = validateGroups([entry({ slug: 'a' }), entry({ slug: 'b' })]);
  assert.deepEqual(halts, []);
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
    `Duplicate chapter slug 'dup-slug' — chapter slugs must be globally unique across all groups (wikilinks and Quartz-shortest resolution key on the basename).`,
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
  assert.equal(oldTarget.sameContainerAsNew, false, 'F2: the exactly-one exception is bare-wikilink-only');
  assert.equal(oldTarget.expectedMatchCount, 0, 'F2: path-mode always expects the old target GONE');
  assert.equal(findFact(facts, 'title-container'), undefined, 'unchanged title carries no title fact');
});

test('R14-F3 exactly-one exception: same title-preserving move in WIKILINK mode (F2 split fixture)', () => {
  // Same title-preserving group-slug move as above, but wikilinks: true — here old and new lines
  // ARE the textually identical `[[slug]]` string, so the exactly-one-match-under-shared-container
  // exception is the sound fact (there is no separate "old spelling" to look for).
  const p = profile({ publish: { wikilinks: true } });
  const old = entry({ group: 'admin', group_title: 'Admin' });
  const next = entry({ group: 'management', group_title: 'Admin' });
  const facts = manualMigrationChecklist(p, old, next);

  const oldTarget = findFact(facts, 'old-index-target-gone');
  assert.equal(oldTarget.form, 'wikilink');
  assert.equal(oldTarget.oldContainerTitle, 'Admin');
  assert.equal(oldTarget.sameContainerAsNew, true);
  assert.equal(oldTarget.expectedMatchCount, 1);
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
  assert.equal(oldTarget.sameContainerAsNew, false);
  assert.equal(oldTarget.expectedMatchCount, 0);
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
  assert.equal(findFact(facts, 'old-index-target-gone').expectedMatchCount, 0);
  const noSink = findFact(facts, 'no-live-capture-sink');
  assert.equal(noSink.oldDirQualified, 'vault/handbook/assets/admin/items');
  assert.equal(noSink.oldDirTail, 'admin/items');
  const noWikilink = findFact(facts, 'no-forbidden-wikilink');
  assert.equal(noWikilink.slug, 'items');
  assert.equal(noWikilink.oldChapterRelPath, 'vault/handbook/admin/items.md');
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

test('R14-F3 same-title group-move: exactly-one-match-under-shared-container rule via locateChapterLine', () => {
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
