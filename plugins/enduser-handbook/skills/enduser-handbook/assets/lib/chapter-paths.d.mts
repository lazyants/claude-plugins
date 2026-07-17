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
// addition to capture.output_dir (currently only manualMigrationChecklist).
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

/** See chapter-paths.mjs: the shipped 1.4.1 static-md partial-concatenation embed, quirk included. */
export function legacyStaticEmbedPath(chapterFile: string, outputDir: string, slug: string, file: string): string;

/** See chapter-paths.mjs: static-md's mode selector between the two embed forms above. */
export function staticEmbedPath(
  entries: ChapterEntry[],
  chapterFile: string,
  profileLike: CaptureProfileLike,
  entry: ChapterEntry,
  file: string,
): string;

/** See chapter-paths.mjs: all D1 manifest-review gates; [] for a group-free manifest. */
export function validateGroups(entries: ChapterEntry[]): string[];

/** See chapter-paths.mjs: the D6 step-0 index-line idempotency check. */
export function locateChapterLine(indexLines: string[], expectedTarget: string): LocateChapterLineResult;

/** See chapter-paths.mjs: the D6 container-resolution classifier. */
export function findContainer(indexLines: string[], groupTitle: string): FindContainerResult;

/** See chapter-paths.mjs: the D6 manual-migration boundary trigger. */
export function groupChanges(oldEntries: ChapterEntry[], newEntries: ChapterEntry[]): GroupChangesResult;

/** See chapter-paths.mjs: the per-delta-kind terminal-state fact descriptors. */
export function manualMigrationChecklist(
  profileLike: ProfileLike,
  oldEntry: ChapterEntry | null,
  newEntry: ChapterEntry | null,
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
