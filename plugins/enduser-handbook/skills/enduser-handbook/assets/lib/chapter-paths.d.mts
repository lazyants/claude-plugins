// enduser-handbook capture asset — non-normative reference implementation for the Playwright
// reference case. The normative, engine-agnostic contract lives in
// references/manifest-discipline.md, references/publish-targets/obsidian-vault.md,
// references/publish-targets/static-md.md, and references/revalidation.md (the D1-D6 design).
// Engine-neutral: reused as-is by any engine's driver glue and by capture.example.spec.ts.
//
// chapter-paths.d.mts — TypeScript declarations for chapter-paths.mjs so a downstream
// typechecking project resolves the .ts -> .mjs import. This repo does not compile TypeScript.

export interface ChapterEntry {
  slug: string;
  group?: string;
  group_title?: string;
}

// The capture-only subset of the profile — everything chapterAssetDir/staticEmbedPath actually
// read at runtime (never `publish`). A capture spec (capture.example.spec.ts) legitimately never
// constructs `publish.*` (it is not the publish step), so requiring the full ProfileLike there
// would be a type error against real call sites (F3) — split the contract instead of overclaiming
// a dependency the function doesn't have.
export interface CaptureProfileLike {
  capture: { output_dir: string };
}

// The full profile shape — required by functions that resolve index/chapters-dir paths in
// addition to capture.output_dir (currentIndexExpectedTarget and manualMigrationChecklist).
export interface ProfileLike extends CaptureProfileLike {
  publish: { chapters_dir: string; index_file: string; wikilinks: boolean };
}

export interface LocateChapterLineMatch {
  line: string;
  containerTitle: string | null;
}

// R5-F1/F2: the SAME classification findContainer's non-heading branch uses, computed over the
// SAME sanitized view — 'headings' iff the index has >=1 depth>=2 heading and no YAML-mapping
// structure outside a leading frontmatter block; 'non-heading' otherwise.
export type IndexForm = 'headings' | 'non-heading';

export interface LocateChapterLineOptions {
  // D6, default false: fold ONE terminal '.md' (case-insensitive) off both the wanted target and
  // every extracted line target before comparison.
  wikilink?: boolean;
}

// D7: the classifyChapterWiring outcome — see chapter-paths.mjs for the full dedup-guard/D8
// placement-separation contract.
export type ChapterWiringClassification = 'absent' | 'canonical' | 'legacy' | 'duplicate';

export interface LocateChapterLineResult {
  present: boolean;
  // R5-F1: null is ambiguous on its own — it means EITHER "non-heading file" OR "an active line
  // before any container / after a depth-1 heading reset in a HEADINGS-form file" (uncontained).
  // Disambiguate via `indexForm`: 'headings' + null => uncontained (a wrong-placement case the
  // caller halts via containerTitleMatches returning false); 'non-heading' + null => the ordinary
  // membership-only case.
  containerTitle: string | null;
  multiple: boolean;
  indexForm: IndexForm;
  matches: LocateChapterLineMatch[];
}

export interface FindContainerHeading {
  index: number;
  depth: number;
  title: string;
}

export type FindContainerResult =
  | { kind: 'zero'; headingDepth: number }
  | { kind: 'single'; location: FindContainerHeading }
  | { kind: 'multiple'; matches: FindContainerHeading[] }
  | { kind: 'non-heading' };

// #223 [1.10.0] — a nested-list container bullet that matched the trimmed group_title.
export interface NestedContainerMatch {
  index: number;
  label: string;
}

// #223 [1.10.0] — the wireNestedListChapter outcome. 'inserted' carries the fully-mutated index
// (newLines.join('\n') reproduces the exact file bytes, EOL + terminal-newline preserved);
// 'multiple' lists the >=2 ambiguous container bullets (adapter halts); 'not-a-list' means the
// index is outside the bounded safe subset (caller keeps today's manual halt, byte-identical).
export type WireNestedListChapterResult =
  | { kind: 'inserted'; created: boolean; newLines: string[] }
  | { kind: 'multiple'; matches: NestedContainerMatch[] }
  | { kind: 'not-a-list' };

export interface EntryChange {
  kind: 'group-change' | 'title-change' | 'group-and-title-change' | 'removal';
  slug: string;
  oldEntry: ChapterEntry | null;
  newEntry: ChapterEntry | null;
}

export interface GroupChangesResult {
  changes: EntryChange[];
  anyGroupFlip: boolean;
}

export interface MigrationFact {
  kind: string;
  [key: string]: unknown;
}

export interface ScanFailure {
  chapter: string;
  line: number;
  target: string;
}

/** See chapter-paths.mjs: the D1 activation gate — true iff any entry carries `group`. */
export function anyGroup(entries: ChapterEntry[]): boolean;

/** See chapter-paths.mjs: D2 chapter path relative to publish.chapters_dir. */
export function chapterRelPath(entry: ChapterEntry): string;

/** See chapter-paths.mjs: D3 group-mirrored asset dir. */
export function chapterAssetDir(profileLike: CaptureProfileLike, entry: ChapterEntry): string;

/** See chapter-paths.mjs: the canonical full-target embed formula. */
export function embedPath(chapterFile: string, assetDir: string, filename: string): string;

/** See chapter-paths.mjs: the superseded 1.4.1 static-md partial-concatenation embed, quirk included — retained for exported-API compatibility, no longer called by staticEmbedPath [1.6.0]. */
export function legacyStaticEmbedPath(chapterFile: string, outputDir: string, slug: string, file: string): string;

/** See chapter-paths.mjs: #220 [1.6.0] the write-time canon — always the full-target embed formula, regardless of anyGroup; `entries` is retained for exported-API compatibility but no longer consulted. */
export function staticEmbedPath(
  entries: ChapterEntry[],
  chapterFile: string,
  profileLike: CaptureProfileLike,
  entry: ChapterEntry,
  file: string,
): string;

export interface ValidateGroupsOptions {
  // #310 [1.9.0], default false: scope slug uniqueness PER GROUP (publish.per_group_slug_uniqueness)
  // — two chapters in different groups may reuse a slug; a duplicate within one group still halts.
  perGroupSlugs?: boolean;
}

/** See chapter-paths.mjs: all D1 manifest-review gates; [1.6.0, #221] a group-free manifest now halts unconditionally on a duplicate flat slug instead of always returning []; [1.9.0, #310] options.perGroupSlugs (default false) scopes slug uniqueness per group. */
export function validateGroups(entries: ChapterEntry[], options?: ValidateGroupsOptions): string[];

/** See chapter-paths.mjs: the D6 step-0 index-line idempotency check; options.wikilink (default false) folds ONE terminal '.md' off both sides before comparison. */
export function locateChapterLine(
  indexLines: string[],
  expectedTarget: string,
  options?: LocateChapterLineOptions,
): LocateChapterLineResult;

/** See chapter-paths.mjs: [1.8.0] #295 — the exported D6 index-target formula; vaultRelChaptersDir is required (and validated) in wikilinks mode, ignored in path mode. */
export function currentIndexExpectedTarget(
  profileLike: ProfileLike,
  entry: ChapterEntry,
  vaultRelChaptersDir?: string,
): string;

/** See chapter-paths.mjs: [1.8.0] #294 D7 — the single union-count wiring classifier over two locateChapterLine scans; answers target-string presence/form only, never placement (D8). */
export function classifyChapterWiring(
  qualifiedTarget: string,
  legacyBareTarget: string,
  qScan: LocateChapterLineResult,
  lScan: LocateChapterLineResult,
): ChapterWiringClassification;

/** See chapter-paths.mjs: the D6 container-resolution classifier. */
export function findContainer(indexLines: string[], groupTitle: string): FindContainerResult;

/** See chapter-paths.mjs: #223 [1.10.0] pure nested-list (GitBook SUMMARY.md) grouped-index write automation, absent-line path only — returns the fully-mutated index, a multiple-container halt, or 'not-a-list' (outside the bounded safe subset). */
export function wireNestedListChapter(
  indexLines: string[],
  groupTitle: string,
  chapterLink: string,
): WireNestedListChapterResult;

/** See chapter-paths.mjs: #223 [1.10.0] escape-aware whole-content link/wikilink label unwrap (else the trimmed content verbatim) — the display text matched against a group_title. */
export function extractLabel(content: string): string;

/** See chapter-paths.mjs: #223 [1.10.0] the §5.1 positive plain-label allowlist (`s` already trimmed) — true iff the label's rendered form equals its literal form. */
export function isPlainLabel(s: string): boolean;

/** See chapter-paths.mjs: the D6 manual-migration boundary trigger. */
export function groupChanges(oldEntries: ChapterEntry[], newEntries: ChapterEntry[]): GroupChangesResult;

/** See chapter-paths.mjs: the per-delta-kind terminal-state fact descriptors; vaultRelChaptersDir is threaded into every currentIndexExpectedTarget call this function makes (wikilinks mode). */
export function manualMigrationChecklist(
  profileLike: ProfileLike,
  oldEntry: ChapterEntry | null,
  newEntry: ChapterEntry | null,
  vaultRelChaptersDir?: string,
): MigrationFact[];

/** See chapter-paths.mjs: the production D6 halt-text formatter (exact strings). */
export function renderManualMigrationHalt(
  changes: EntryChange[],
  checklists: MigrationFact[][],
  scanFailures?: ScanFailure[],
): string;

/** See chapter-paths.mjs: the two-sided boundary-aware capture-spec red-flag predicate. */
export function specReferencesDir(specText: string, dir: string): boolean;

/** See chapter-paths.mjs: the forbidden-target wikilink classification predicate. */
export function chapterHasWikilinkTo(chapterText: string, slug: string, oldChapterRelPath: string): boolean;

/** See chapter-paths.mjs: the trim-safe step-0 "line present under the correct container" comparator. */
export function containerTitleMatches(containerTitle: string | null, entry: ChapterEntry): boolean;
