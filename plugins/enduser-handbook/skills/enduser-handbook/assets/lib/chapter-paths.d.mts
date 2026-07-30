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
  // 1.11.0 #330 review fix: this match's position into indexView(indexLines), so a caller (the
  // present-line placement verifier) needing a line index can delegate to this loop instead of
  // re-implementing it.
  index: number;
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
// [1.11.0] 'unwritable' means the writer's own reader would REFUSE the bytes the writer was about to
// hand back, so nothing was written and there is no index to persist. `field` names the manifest
// value at fault ('title' or 'group_title'; 'unknown' when substituting either stand-in still fails),
// derived by substitution rather than by inspecting the value, so it stays correct for causes nobody
// has enumerated. A caller MUST halt and name that field: retrying cannot help, and the previous
// behaviour — writing the line anyway — left the index permanently unreadable to this module, for
// every chapter and every group in it, from a manifest value nothing upstream rejects. The check is
// deliberately conservative: it can decline a value that would in fact have round-tripped, which is
// the right direction for a tool that rewrites a file it does not own.
//
// [1.11.0] 'present' means the resolved container already carries this exact chapter link, so
// nothing was written and there is no index to persist. `index` is a 0-BASED index into the
// CALLER's own `indexLines` array, not into any internal view — verified to hold across a leading
// frontmatter block (which the writer blanks rather than removes) and a CRLF file (whose elements
// keep their trailing '\r'). Decided by comparing the bullet's CONTENT verbatim against the
// caller's `chapterLink`, deliberately not through step 0's target parse: that COVERS step 0's
// blind spot (a row whose own text defeats the target parse) without being CONFINED to it —
// measured, a row step 0 does recognize still makes the writer answer 'present'.
// A PUBLISH-PATH caller — one wiring a real chapter link — MUST halt on it: retrying the same
// unchanged call can only return the same 'present' outcome. The in-module probe caller is
// deliberately outside that requirement: verifyNonHeadingPlacement's fixed probe link can
// literally be a row in the index, and its rule-4 accept-list reads 'present' as shape
// recognition and continues to 'ok'.
export type WireNestedListChapterResult =
  | { kind: 'inserted'; created: boolean; newLines: string[] }
  | { kind: 'present'; index: number }
  | { kind: 'unwritable'; field: 'title' | 'group_title' | 'unknown' }
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

/** See chapter-paths.mjs: [1.11.0] #330 the sanitized view locateChapterLine scans, extracted so every caller reaches it through this export rather than re-deriving the expression inline. */
export function indexView(indexLines: string[]): string[];

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

export interface LeadingFrontmatterSpan {
  start: 0;
  endExclusive: number;
}

/** See chapter-paths.mjs: [1.11.0] #330 the narrow test-seam projection of the writer's private line-preparation call (prepareIndexLines) — {kind, span} only; span is null when the index carries no leading frontmatter block. Reached by tests alone: every production caller needing this preparation state calls the private helper directly instead, whoever they are. */
export function leadingFrontmatterSpan(
  indexLines: string[],
): { kind: 'not-a-list' } | { kind: 'ok'; span: LeadingFrontmatterSpan | null };

/** See chapter-paths.mjs: #223 [1.10.0] pure nested-list (GitBook SUMMARY.md) grouped-index write automation, absent-line path only — returns the fully-mutated index on success, or one of the refusals WireNestedListChapterResult declares and documents above: [1.11.0] 'present' when the container already carries this exact link (literal membership that step 0's target parser cannot provide), [1.11.0] 'unwritable' when the writer's own reader would refuse the bytes it was about to hand back (nothing written; `field` names the manifest value at fault), a multiple-container halt, or 'not-a-list' (outside the bounded safe subset). */
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

/** See chapter-paths.mjs: the per-delta-kind terminal-state fact descriptors; vaultRelChaptersDir is threaded into every currentIndexExpectedTarget call this function makes (wikilinks mode). [1.12.0] provenanceActive (default false) gates the twelfth fact kind, 'provenance-record' ({kind, oldPath, newPath: string|null}) — the caller's own re-assertion of this run's W1 ownership outcome, never inferred from disk; absent (or false) reproduces every pre-1.12.0 checklist byte-for-byte. Present only for 'removal' and the two grouped-change kinds, never 'title-change'. */
export function manualMigrationChecklist(
  profileLike: ProfileLike,
  oldEntry: ChapterEntry | null,
  newEntry: ChapterEntry | null,
  vaultRelChaptersDir?: string,
  provenanceActive?: boolean,
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

/**
 * Options for verifyNonHeadingPlacement. `wikilink` is the ONLY option, and carries the same
 * meaning as locateChapterLine's (chapter-paths.mjs, the `{ wikilink = false }` destructure):
 * it selects the wikilink target spelling over the path spelling. Named rather than reused so this
 * API is not coupled to LocateChapterLineOptions, which is unrelated.
 */
export interface VerifyNonHeadingPlacementOptions {
  wikilink?: boolean;
}

/**
 * Named rather than inlined so each variant is pinnable.
 * `misplaced` is the only variant carrying a payload.
 */
export type VerifyNonHeadingPlacementResult =
  | { kind: 'ok' }
  | { kind: 'misplaced'; foundContainer: string | null }
  | { kind: 'inconsistent' }
  | { kind: 'unverifiable' };

/**
 * See chapter-paths.mjs: present-line placement verification for the nested-list index form (#330).
 * `selectedTarget` is the target the CALLER already selected — the Obsidian adapter scans the
 * qualified and legacy-bare spellings and picks one before placement checking, so passing the
 * selected one lets a legitimately-present legacy row verify instead of reporting `inconsistent`.
 */
export function verifyNonHeadingPlacement(
  indexLines: string[],
  selectedTarget: string,
  groupTitle: string,
  options?: VerifyNonHeadingPlacementOptions,
): VerifyNonHeadingPlacementResult;

// ---------------------------------------------------------------------------------------------
// [1.12.0] Image-destination API — see chapter-paths.mjs's own "Image-destination API" section.
// ---------------------------------------------------------------------------------------------

/** See chapter-paths.mjs: the bounded balanced-paren / angle-wrapped destination-group scanner behind every markdown-link-shaped recognizer in this module — now exported so a consumer can reuse the same scanner rather than re-implementing it. */
export function findMarkdownLinkGroups(line: string): string[];

export interface StripInertContextsOptions {
  /**
   * [1.12.0] default false. When true, a fence-shaped run (backtick/tilde, length >= 3, at true
   * line start) whose own column is >= 4 (tab-expanded) is NOT recognized as a fence — it is an
   * indented code block and is left untouched rather than blanked. Default false preserves every
   * pre-1.12.0 caller's behavior byte-for-byte; expectedAssets is the only caller passing true.
   */
  indentedRunIsCode?: boolean;
}

/** See chapter-paths.mjs: the shared inert-context stripper (fenced code, inline code spans, HTML comments blanked to equal-length spans) — now exported so a consumer can reuse the same classification the index-file scanners above are built on. */
export function stripInertContexts(text: string, options?: StripInertContextsOptions): string;

/** See chapter-paths.mjs: extracts and escape-decodes a markdown link/image destination from its raw parenthesized (or angle-wrapped) group — now exported so a consumer matching a destination against a candidate set does not need to re-implement the angle/escape handling. */
export function parseMdLinkDestination(raw: string): string;

/** One entry of expectedAssets' asset list: the record key (asset-dir-relative, byte-exact from the directory listing) and the assetDir-qualified path a caller reads/hashes directly. */
export interface EmbedCandidateEntry {
  key: string;
  absPath: string;
}

export type ExpectedAssetsResult =
  | { ok: true; assets: EmbedCandidateEntry[] }
  | { ok: false; halt: { construct: string; line: number } };

/**
 * See chapter-paths.mjs: the ONLY place an embed candidate destination set is constructed —
 * expectedAssets calls this itself, so no caller may hand in a hand-written map. `target` is the
 * RAW profile value ('static_md' / 'obsidian_vault'), never the '-'-hyphenated adapter filename.
 * Throws (an EmbedCandidateHalt, an ordinary Error to a caller outside this module) when any
 * candidate fails the round-trip gate, the closed character-subset gate, or a current/legacy
 * union-collision check.
 */
export function buildEmbedCandidates(
  profileLike: CaptureProfileLike,
  entry: ChapterEntry,
  chapterFile: string,
  filenames: string[],
  target: string,
): Map<string, string>;

/** See chapter-paths.mjs: the structural-only stored-record-key predicate shared by both record readers — rejects a leading '/', an empty segment, and the segments '.'/'..'; constrains no characters. */
export function isCanonicalAssetKey(key: unknown): boolean;

/**
 * See chapter-paths.mjs: the chapter's embedded images, or a halt naming the first construct the
 * bounded extractor cannot account for. Calls buildEmbedCandidates itself.
 */
export function expectedAssets(
  profileLike: CaptureProfileLike,
  entry: ChapterEntry,
  chapterFile: string,
  chapterText: string,
  filenames: string[],
  target: string,
): ExpectedAssetsResult;

// ---------------------------------------------------------------------------------------------
// [1.12.0] W2 preflight gates 1-4 — see chapter-paths.mjs's own section header for why gate 3
// takes injected fs access instead of importing node:fs, and why all four are independently
// callable (W6 must run them itself against a bare entry set).
// ---------------------------------------------------------------------------------------------

/** Gate 1 — slug alphabet, the same pattern validateGroups already uses for `group`. */
export function isValidSlugSyntax(slug: unknown): boolean;

export interface CanonicalPathCollision {
  canonicalPath: string;
  entries: ChapterEntry[];
}

/** Gate 2 — canonical chapterAssetDir() uniqueness across a set of entries; deliberately independent of gate 1 (testable with alphabet-violating inputs directly). */
export function findCanonicalPathCollisions(profileLike: CaptureProfileLike, entries: ChapterEntry[]): CanonicalPathCollision[];

export interface PhysicalContainmentDeps {
  lstat: (path: string) => { isSymbolicLink(): boolean };
  readlink: (path: string) => string;
}

export type PhysicalContainmentHaltReason = 'escapes-root' | 'cycle' | 'inspection-failed';

export type PhysicalContainmentResult =
  | { ok: true; resolved: string }
  | { ok: false; halt: { reason: PhysicalContainmentHaltReason; detail: string } };

/** Gate 3 — physical containment, no-follow, cycle-safe; resolves `dir` against `rootDir` using ONLY deps.lstat/deps.readlink (never a realpath call). */
export function resolvePhysicalContainment(rootDir: string, dir: string, deps: PhysicalContainmentDeps): PhysicalContainmentResult;

export interface PhysicalPathCollision {
  resolvedPath: string;
  entries: ChapterEntry[];
}

/** Gate 4 — pairwise physical uniqueness over already gate-3-resolved directories (the cross-entry property gates 2 and 3 individually cannot see). Trust boundary: each `resolved` value is taken on trust as resolvePhysicalContainment's real output for that entry — this function cannot verify it was not hand-built, the same way `expected` elsewhere in this design is a public shape, deliberately not a capability. */
export function findPhysicalPathCollisions(resolvedEntries: Array<{ entry: ChapterEntry; resolved: string }>): PhysicalPathCollision[];
