// enduser-handbook capture asset — non-normative reference implementation for the Playwright
// reference case. The normative, engine-agnostic contract lives in
// references/manifest-discipline.md, references/publish-targets/obsidian-vault.md,
// references/publish-targets/static-md.md, and references/revalidation.md (the D1-D6 design).
// Engine-neutral: reused as-is by any engine's driver glue and by capture.example.spec.ts.
//
// chapter-paths.mjs — the pure, dependency-free (no node:fs, no node:path — path algebra is
// reimplemented below so the module never depends on the host platform's separator convention)
// group axis helper for the optional `group`/`group_title` manifest fields (issue #19). Every
// exported function is a pure, deterministic, side-effect-free predicate/transform over plain
// data (manifest entries, profile-shaped objects, index-file line arrays, chapter/spec text) so
// the whole group-axis contract is unit-testable (tests/chapter-paths.test.mjs) without touching
// a filesystem or a browser — but NOT total: a narrow, named set of caller-detectable programming
// errors throws rather than resolving ambiguously or answering silently wrong (currentIndexExpectedTarget's
// missing/malformed vaultRelChaptersDir in wikilinks mode; manualMigrationChecklist's omitted
// provenanceActive) — see each function's own "fail loud" contract. Every other input shape the
// module can classify (an invalid `group`, a malformed manifest) is instead reported as an
// ordinary halt-text VALUE, never an exception — throwing is reserved for the narrower case where
// the caller itself made a mistake a return value could not safely paper over.
//
// A group-free manifest (no entry carries `group`) must behave byte-identically to the shipped
// 1.4.1 flat layout in every function here — the activation rule (D1): every new gate/branch is
// gated on `anyGroup` — WITH THREE EXCEPTIONS that are group-free-aware by design and no longer
// consult this gate: staticEmbedPath ([1.6.0] #220 — always writes the full-target embed formula,
// no mode branch), validateGroups ([1.6.0] #221 — a group-free manifest's duplicate flat slug now
// halts unconditionally), and currentIndexExpectedTarget's wikilinks branch ([1.8.0] #294 — a
// group-free wikilinks manifest now emits the vault-root-relative `vaultRelChaptersDir/slug`
// target, not the bare slug). Every other function here still follows the activation rule
// unmodified.

// ---------------------------------------------------------------------------------------------
// Path algebra — private. POSIX-only by construction: segments are split on '/' AND '\\' (so a
// stray backslash from a Windows-authored profile value still normalizes), '.' segments are
// dropped ('./vault/x' == 'vault/x'), and every join/relative below re-derives from segments
// rather than string-concatenating, so the result is always POSIX forward-slash.
//
// F4: profile path fields (capture.output_dir, publish.chapters_dir, publish.index_file) are
// unrestricted strings and MAY be absolute (`/vault/handbook/assets`). rawSegments/normalizeSegments
// discard the leading '/' the same way `.split('/')` always did, so absoluteness is tracked
// SEPARATELY via isAbsolute() and threaded explicitly through posixJoin/posixDirname (the two
// functions whose OUTPUT is a resolved path, not a delta) — otherwise an absolute output_dir would
// silently become a relative one in every derived asset dir, migration fact, and halt path.
// posixRelative's output is always relative-style by definition (no leading slash) regardless of
// its inputs' absoluteness, so it does not need the marker threaded through its RESULT — only its
// segment comparison, which is unaffected by a leading '/' either way.
// ---------------------------------------------------------------------------------------------

function isAbsolute(p) {
  return /^[\\/]/.test(String(p));
}

function rawSegments(p) {
  return String(p)
    .split(/[\\/]+/)
    .filter((seg) => seg !== '' && seg !== '.');
}

// Collapses '..' against a preceding real segment ('a/b/../c' -> 'a/c'). A '..' with nothing to
// pop either disappears (the path is anchored at an absolute root — POSIX collapses '/..' to '/')
// or is kept literally (a relative path climbing above where it started — there is nothing to
// resolve it against, so it must stay in the result).
function normalizeSegments(segments, absolute) {
  const out = [];
  for (const seg of segments) {
    if (seg !== '..') {
      out.push(seg);
      continue;
    }
    if (out.length > 0 && out[out.length - 1] !== '..') {
      out.pop();
    } else if (!absolute) {
      out.push('..');
    }
  }
  return out;
}

function formatPath(segments, absolute) {
  return absolute ? `/${segments.join('/')}` : segments.join('/');
}

// Fully-normalized segments of ONE path, '..' collapsed against the path's own absoluteness —
// for resolved filesystem paths. Contrast pathSegments below, which deliberately stays marker-free.
function resolvedSegments(p) {
  return normalizeSegments(rawSegments(p), isAbsolute(p));
}

// Relative-only normalization for IDENTITY comparisons (wikilink/index-line targets) — these are
// never resolved filesystem paths, so the absolute marker is intentionally discarded, same as
// pre-F4 behavior; only '.'/'..' segment normalization is new.
function pathSegments(p) {
  return normalizeSegments(rawSegments(p), false);
}

function posixJoin(...parts) {
  const absolute = parts.length > 0 && isAbsolute(parts[0]);
  const segments = normalizeSegments(parts.flatMap((p) => rawSegments(p)), absolute);
  return formatPath(segments, absolute);
}

function posixDirname(filePath) {
  const segments = resolvedSegments(filePath);
  segments.pop();
  return formatPath(segments, isAbsolute(filePath));
}

// relative(fromDir, toPath): both sides are segment-normalized first, so the result is always
// POSIX-separated, absolute-marker-free (a relative delta is never itself "absolute"). Degenerates
// to '' (empty string) when fromDir === toPath — this is the exact degenerate case the D3/static-md
// legacy-quirk pin depends on (see legacyStaticEmbedPath below).
//
// F4: fromDir and toPath MUST share the same rootedness. A profile mixing an absolute
// capture.output_dir with a relative publish.chapters_dir/index_file (or vice versa) is a real
// configuration error, not something this function can silently resolve — diffing an absolute
// asset dir against a relative chapter file (or the reverse) discards one path's actual root and
// produces a garbage delta (e.g. `../../project/vault/...`) that LOOKS like a valid path but
// resolves to nothing. Fail loud (throw) rather than ship a silently wrong embed/migration-fact
// path — the boundary-check style used throughout this module (err-visible over err-silent).
function posixRelative(fromDir, toPath) {
  if (isAbsolute(fromDir) !== isAbsolute(toPath)) {
    throw new Error(
      `posixRelative: mixed rootedness between '${fromDir}' and '${toPath}' — chapter_file, ` +
        'asset dir, and index_file must all be absolute together or all relative together.',
    );
  }
  const fromSegs = resolvedSegments(fromDir);
  const toSegs = resolvedSegments(toPath);
  let common = 0;
  while (common < fromSegs.length && common < toSegs.length && fromSegs[common] === toSegs[common]) {
    common += 1;
  }
  const ups = fromSegs.length - common;
  const downs = toSegs.slice(common);
  return [...Array(ups).fill('..'), ...downs].join('/');
}

function normalizeLinkTarget(target) {
  return pathSegments(target).join('/');
}

// ---------------------------------------------------------------------------------------------
// D1 — activation rule
// ---------------------------------------------------------------------------------------------

/**
 * True iff at least one entry carries `group`. The activation gate every D1-D6 branch/behavior is
 * conditioned on — WITH THREE EXCEPTIONS that no longer consult this gate: staticEmbedPath
 * ([1.6.0] #220 — always the full-target embed formula), validateGroups ([1.6.0] #221 — a
 * group-free manifest's duplicate flat slug now halts unconditionally), and
 * currentIndexExpectedTarget's wikilinks branch ([1.8.0] #294 — a group-free wikilinks manifest
 * now emits `vaultRelChaptersDir/slug`, not the bare slug). Every other function here still
 * behaves identically to 1.4.1 when anyGroup(entries) === false.
 *
 * @param {Array<{group?: string}>} entries
 * @returns {boolean}
 */
export function anyGroup(entries) {
  return entries.some((entry) => entry.group !== undefined);
}

// ---------------------------------------------------------------------------------------------
// D2 — chapter path join
// ---------------------------------------------------------------------------------------------

/**
 * The chapter's path RELATIVE TO `publish.chapters_dir` (D2): `<group>/<slug>.md` when `group`
 * is set, `<slug>.md` otherwise. The caller joins this onto `publish.chapters_dir` to get the
 * full path — kept separate from chapterAssetDir's profileLike-taking signature because D2 does
 * not need `capture.output_dir`.
 *
 * @param {{slug: string, group?: string}} entry
 * @returns {string}
 */
export function chapterRelPath(entry) {
  // See outputDirTail's comment: presence is `!== undefined`, matching anyGroup (F1) — never a
  // truthiness check, so a falsy-but-present group value never silently derives a flat path.
  return entry.group !== undefined ? `${entry.group}/${entry.slug}.md` : `${entry.slug}.md`;
}

// Full chapters_dir-qualified chapter path — a small private convenience built on chapterRelPath,
// used everywhere a fully-resolved path (not just the chapters_dir-relative tail) is needed.
function chapterFullPath(profileLike, entry) {
  return posixJoin(profileLike.publish.chapters_dir, chapterRelPath(entry));
}

// ---------------------------------------------------------------------------------------------
// D3 — group-mirrored asset tree
// ---------------------------------------------------------------------------------------------

// The asset-dir tail under capture.output_dir — `<group>/<slug>` when grouped, `<slug>` when
// flat. Shared by chapterAssetDir (D3) and the capture-spec migration facts (oldDirTail, D6).
// PRESENCE test is `!== undefined` — the SAME predicate anyGroup/validateGroups use (F1) — never
// truthiness: a falsy-but-present group (0, false, '', null) must derive the GROUPED form, not
// silently fall back to flat, so a bad group value can never disagree with anyGroup's verdict.
// validateGroups is the gate that rejects non-string/malformed group values before this ever
// runs on real data; this function may assume post-validation input.
function outputDirTail(entry) {
  return entry.group !== undefined ? `${entry.group}/${entry.slug}` : entry.slug;
}

/**
 * chapterAssetDir(entry) = join(capture.output_dir, entry.group?, entry.slug) — D3. Depends only
 * on the entry itself (not on whether OTHER entries in the manifest are grouped), so it is
 * activation-independent by construction: a flat entry in an anyGroup manifest gets the exact
 * same dir as it would in a group-free manifest.
 *
 * @param {{capture: {output_dir: string}}} profileLike
 * @param {{slug: string, group?: string}} entry
 * @returns {string}
 */
export function chapterAssetDir(profileLike, entry) {
  return posixJoin(profileLike.capture.output_dir, outputDirTail(entry));
}

/**
 * The ONE canonical write-time-canon embed formula (D6): full-target relative(). staticEmbedPath
 * uses this for every new or re-authored write, in every mode (group-free, flat-under-anyGroup,
 * grouped) — it has no mode branch. A pre-1.6.0 group-free chapter that already used the legacy
 * partial-concatenation spelling (legacyStaticEmbedPath) keeps that existing spelling; nothing
 * rewrites it retroactively (the deferred repair is #246).
 *
 * embedPath(chapterFile, assetDir, filename) = relative(dirname(chapterFile), join(assetDir, filename))
 *
 * @param {string} chapterFile
 * @param {string} assetDir  typically chapterAssetDir(profileLike, entry)
 * @param {string} filename
 * @returns {string}
 */
export function embedPath(chapterFile, assetDir, filename) {
  return posixRelative(posixDirname(chapterFile), posixJoin(assetDir, filename));
}

/**
 * The superseded 1.4.1 spelling, retained for exported-API compatibility [1.6.0]: the partial
 * concatenation `<rel>/<slug>/<file>` where `<rel> = relative(dirname(chapterFile), outputDir)`,
 * quirk included — degenerates to a LEADING SLASH (`/<slug>/<file>`) when
 * `dirname(chapterFile) === outputDir` (rel === ''). staticEmbedPath no longer calls this
 * function — #220 dropped the anyGroup branch that used to select between the two spellings — it
 * stays exported only because it is public API with zero in-repo callers (F8).
 *
 * @param {string} chapterFile
 * @param {string} outputDir  profileLike.capture.output_dir
 * @param {string} slug
 * @param {string} file
 * @returns {string}
 */
export function legacyStaticEmbedPath(chapterFile, outputDir, slug, file) {
  const rel = posixRelative(posixDirname(chapterFile), outputDir);
  return `${rel}/${slug}/${file}`;
}

/**
 * #220 write-time canon [1.6.0]: ALWAYS the full-target formula (embedPath), regardless of
 * anyGroup — the mode branch that shipped in 1.5.0 (group-free kept the legacy leading-slash
 * quirk, anyGroup switched to the full-target form) is dropped (F1a: one of the two 1.6.0
 * exceptions to the activation rule, D1). This governs NEW writes only — an already-written
 * group-free chapter that predates 1.6.0 keeps whatever spelling it already has; there is no
 * automatic retroactive repair (see references/revalidation.md's "Write-time canon" section).
 *
 * `entries` is RETAINED for exported-API compatibility (F8: zero in-repo callers, but it is
 * public API) — it is NO LONGER CONSULTED; the anyGroup(entries) branch it used to feed is gone.
 *
 * @param {Array<{group?: string}>} entries  retained for exported-API compatibility; no longer consulted
 * @param {string} chapterFile
 * @param {{capture: {output_dir: string}}} profileLike
 * @param {{slug: string, group?: string}} entry
 * @param {string} file
 * @returns {string}
 */
export function staticEmbedPath(entries, chapterFile, profileLike, entry, file) {
  return embedPath(chapterFile, chapterAssetDir(profileLike, entry), file);
}

// ---------------------------------------------------------------------------------------------
// D1 — manifest-review gates
// ---------------------------------------------------------------------------------------------

const GROUP_PATTERN = /^[a-z0-9]+(-[a-z0-9]+)*$/;
const RESERVED_NAME = 'assets';

// F5: a group_title is "usable" only if it is a genuine string with real (non-whitespace)
// content — a number, boolean, or whitespace-only string can never anchor a heading match (a
// heading's parsed title is always a trimmed, real string) and must be treated the same as a
// missing title, using the EXISTING missing-title halt (no new halt string — the plan's halt
// texts are a byte-contract). trimmedTitle is the single source of truth every OTHER touchpoint
// (findContainer, manualMigrationChecklist, renderManualMigrationHalt) reads through, so a
// padded-but-otherwise-valid title ('  Admin  ') converges and renders identically to 'Admin'
// everywhere, instead of failing strict-equality container matching in only some call sites.
function isUsableTitle(value) {
  return typeof value === 'string' && value.trim() !== '';
}

function trimmedTitle(entry) {
  return isUsableTitle(entry.group_title) ? entry.group_title.trim() : entry.group_title;
}

// R3-F1: locateChapterLine's `containerTitle` is ALREADY trimmed (heading[2].trim()), but the
// step-0 "line present under the correct container ⇒ wiring complete" check the adapter docs
// describe compares it against the MANIFEST entry's raw `group_title` — which the caller has no
// reason to trim itself unless told to. A naive `containerTitle === entry.group_title` therefore
// still fails for a padded title ('  Admin  ' !== 'Admin') even though locateChapterLine's own
// output is clean — findContainer's trim-safety never even runs for this case, since it is only
// reached when step 0 found NO existing line. Exported (the same pattern as specReferencesDir/
// chapterHasWikilinkTo: a narrow production predicate the adapter-authored wiring code calls)
// so the comparison is trim-safe wherever it is made, not just inside this module.
export function containerTitleMatches(containerTitle, entry) {
  return containerTitle !== null && containerTitle === trimmedTitle(entry);
}

/**
 * #221 [1.6.0]: the duplicate-flat-slug halt, extracted from validateGroups' gate 3 so the SAME
 * gate (frozen halt-text and Map-insertion/first-seen order) runs both inside the grouped gate
 * sequence (`groupFree: false` — unchanged 1.5.0 literal/position) AND, new in 1.6.0,
 * unconditionally for a group-free manifest (`groupFree: true` — F1a: the other of the two
 * 1.6.0 activation-rule exceptions, alongside staticEmbedPath). The two literals differ because a
 * group-free duplicate has no group axis to describe — see the halt-text contract on each branch.
 *
 * #310 [1.9.0], `perGroupSlugs` (default false ⇒ pre-1.9.0 behavior byte-for-byte): when true,
 * slug uniqueness is scoped PER GROUP rather than global. A GROUPED entry then keys on
 * `<group><NUL><slug>` (a real NUL — it can never appear in a kebab group/slug, so the composite
 * can never alias a different group/slug pair), so two chapters in DIFFERENT groups may reuse a
 * slug (distinct group subdirectories ⇒ no file-tree collision) while a duplicate WITHIN one group
 * still halts. Every other case keys on the bare slug: the opt-in off, OR a flat (group-less) entry
 * even under the opt-in — flat chapters share the one file-tree namespace regardless of the flag,
 * so their global-uniqueness constraint is unchanged. The per-COLLIDING-BUCKET literal choice keys
 * on whether that bucket carried a group into its key: a bucket keyed WITH a group (perGroupSlugs
 * grouped) renders the group-scoped literal; every other colliding bucket renders the existing
 * group-free (`groupFree`) or global (default) literal unchanged. Default (perGroupSlugs=false) ⇒
 * every key is the bare slug ⇒ no bucket ever carries a group ⇒ identical to the pre-1.9.0 gate.
 *
 * @param {Array<{slug: string, group?: string}>} entries
 * @param {{groupFree: boolean, perGroupSlugs?: boolean}} options
 * @returns {string[]}
 */
function duplicateSlugHalts(entries, { groupFree, perGroupSlugs = false }) {
  const NUL = String.fromCharCode(0);
  const halts = [];
  // Map<key, {count, slug, group}> — `group` is set on the bucket ONLY when the key was composed
  // WITH a group (perGroupSlugs && a grouped entry); it drives the per-bucket literal choice below.
  const seen = new Map();
  for (const entry of entries) {
    // Key per-group ONLY for a WELL-FORMED group — the same format validator gate 1 uses
    // (GROUP_PATTERN). A malformed group (a blank YAML `group:` ⇒ null, `group: ''`, a non-string,
    // or any non-kebab value) falls back to the bare-slug (global) key, so it never renders a
    // misleading "within group 'null'" literal and null/'' never alias onto the same `<NUL><slug>`
    // bucket. Gate 1 is the sole halt for such a manifest — this predicate only decides which
    // duplicate-scope key a malformed entry takes, never whether it is reported.
    const keyedByGroup = perGroupSlugs && typeof entry.group === 'string' && GROUP_PATTERN.test(entry.group);
    const key = keyedByGroup ? [entry.group, entry.slug].join(NUL) : entry.slug;
    const record = seen.get(key);
    if (record === undefined) {
      seen.set(key, { count: 1, slug: entry.slug, group: keyedByGroup ? entry.group : undefined });
    } else {
      record.count += 1;
    }
  }
  for (const { count, slug, group } of seen.values()) {
    if (count > 1) {
      halts.push(
        group !== undefined
          ? `Duplicate chapter slug '${slug}' within group '${group}' — with publish.per_group_slug_uniqueness enabled, chapter slugs must be unique within each group; a duplicate silently overwrites the chapter file and its asset dir.`
          : groupFree
            ? `Duplicate chapter slug '${slug}' — chapter slugs must be unique; a duplicate silently overwrites the chapter file and its asset dir.`
            : `Duplicate chapter slug '${slug}' — chapter slugs must be globally unique across all groups (chapter basenames stay unambiguous across the handbook for the file tree, user-authored bare wikilinks, and Quartz-shortest bare-name resolution).`,
      );
    }
  }
  return halts;
}

/**
 * All D1 manifest-review gates, in one pass. Returns an array of exact halt-text strings (the D6
 * "Halt texts" contract) — empty when the manifest is clean. Group-free manifests [1.6.0, #221]
 * now run exactly one gate — duplicateSlugHalts — and HALT unconditionally on a duplicate flat
 * slug; the shipped 1.4.1 silent-overwrite behavior for that case is gone (F1a: the other of the
 * two 1.6.0 activation-rule exceptions, alongside staticEmbedPath). Gates 1, 2, 4, 5, and 6 below
 * still run only when anyGroup(entries) is true.
 *
 * #310 [1.9.0], `options.perGroupSlugs` (default false): threaded verbatim into BOTH
 * duplicateSlugHalts calls (the group-free early return and gate 3). When true, slug uniqueness is
 * scoped per group — see duplicateSlugHalts' own contract. A 1-arg / options-absent call defaults
 * perGroupSlugs to false, so the whole gate is byte-for-byte the pre-1.9.0 behavior. The option is
 * inert on the group-free early-return branch (no entry carries a group there) but is passed anyway
 * so the two call sites stay uniform.
 *
 * @param {Array<{slug: string, group?: string, group_title?: string}>} entries
 * @param {{perGroupSlugs?: boolean}} [options]
 * @returns {string[]}
 */
export function validateGroups(entries, { perGroupSlugs = false } = {}) {
  if (!anyGroup(entries)) return duplicateSlugHalts(entries, { groupFree: true, perGroupSlugs });
  const halts = [];

  // 1. group regex/one-level + reserved group name. The type check runs BEFORE the regex — a
  // regex .test() coerces its argument to a string, so `null`/`false`/`0`/`123` would otherwise
  // stringify to "null"/"false"/"0"/"123" and silently PASS as "valid" kebab groups (F1: every
  // one of those strings happens to match the kebab pattern). group must be a genuine non-empty
  // string, not merely stringify to one — anything else is unconditionally the same halt a
  // malformed string gets, so a blank YAML `group:` (parses as null) is never silently invisible.
  for (const entry of entries) {
    if (entry.group === undefined) continue;
    if (typeof entry.group !== 'string' || !GROUP_PATTERN.test(entry.group)) {
      halts.push(`Invalid group '${entry.group}' — group must be English kebab-case, one level (no '/').`);
      continue; // an invalid group name cannot be meaningfully checked by the gates below
    }
    if (entry.group === RESERVED_NAME) {
      halts.push(`group 'assets' is reserved (co-location follow-up; keeps the tree unambiguous).`);
    }
  }

  // 2. reserved slug — grouped manifests only (we're already inside the anyGroup branch).
  for (const entry of entries) {
    if (entry.slug === RESERVED_NAME) {
      halts.push(`slug 'assets' is reserved in a grouped manifest (co-location follow-up; keeps the tree unambiguous).`);
    }
  }

  // 3. slug uniqueness, GLOBAL across all entries. #221 [1.6.0]: extracted into
  // duplicateSlugHalts so the SAME gate also runs unconditionally for a group-free manifest via
  // the early return above — this gate is no longer grouped-only, but its position and literal
  // here (groupFree: false) are byte-unchanged from 1.5.0. #310 [1.9.0]: `perGroupSlugs` is
  // threaded through; when true this becomes per-group scope (flat entries still key globally).
  halts.push(...duplicateSlugHalts(entries, { groupFree: false, perGroupSlugs }));

  // 4. group-vs-flat-slug collision — a directory (group) and a chapter file (flat slug) cannot
  // share the same path segment under publish.chapters_dir.
  const groupNames = new Set(entries.filter((e) => e.group !== undefined).map((e) => e.group));
  const flatSlugs = new Set(entries.filter((e) => e.group === undefined).map((e) => e.slug));
  for (const g of groupNames) {
    if (flatSlugs.has(g)) {
      halts.push(
        `group '${g}' collides with flat chapter slug '${g}' — a directory and a chapter file cannot share the same path under publish.chapters_dir.`,
      );
    }
  }

  // 5. group_title — required on every grouped entry, identical across the group. F5: "required"
  // means USABLE (isUsableTitle) — a number, boolean, or whitespace-only string can never anchor
  // a real heading match, so it gets the same missing-title halt as an absent field, never a new
  // halt string. Distinct-title comparison runs on the TRIMMED form so padding alone ('Admin' vs
  // '  Admin  ') is not a false conflict.
  const entriesByGroup = new Map();
  for (const entry of entries) {
    if (entry.group === undefined) continue;
    if (!entriesByGroup.has(entry.group)) entriesByGroup.set(entry.group, []);
    entriesByGroup.get(entry.group).push(entry);
  }
  for (const [group, groupEntries] of entriesByGroup) {
    for (const entry of groupEntries) {
      if (!isUsableTitle(entry.group_title)) {
        halts.push(
          `Entry '${entry.slug}' in group '${group}' lacks group_title — every grouped entry carries the localized group title (never derived from the English group slug).`,
        );
      }
    }
    const distinctTitles = [...new Set(groupEntries.filter((e) => isUsableTitle(e.group_title)).map(trimmedTitle))];
    if (distinctTitles.length > 1) {
      halts.push(
        `Group '${group}' carries conflicting group_title values (${distinctTitles.map((t) => `'${t}'`).join(', ')}) — align all entries of the group.`,
      );
    }
  }

  // 6. group_title — unique ACROSS groups (containers are located by title), trimmed comparison.
  const groupByTitle = new Map();
  for (const [group, groupEntries] of entriesByGroup) {
    const usableEntry = groupEntries.find((e) => isUsableTitle(e.group_title));
    if (usableEntry === undefined) continue;
    const title = trimmedTitle(usableEntry);
    const otherGroup = groupByTitle.get(title);
    if (otherGroup !== undefined && otherGroup !== group) {
      halts.push(
        `Groups '${otherGroup}' and '${group}' share group_title '${title}' — nav containers are located by title; give each group a distinct localized title.`,
      );
    } else {
      groupByTitle.set(title, group);
    }
  }

  return halts;
}

// ---------------------------------------------------------------------------------------------
// D6 — index wiring
// ---------------------------------------------------------------------------------------------

const HEADING_RE = /^(#{1,6})\s+(.*)$/;
const WIKILINK_TARGET_RE = /\[\[([^\]|#^]+)[^\]]*\]\]/g;

// R6: the class invariant every scanner/parser in this section follows. Two DISTINCT escape
// duties, never conflated: (1) SKIPPING — before treating any character as a construct delimiter
// (a fence/code-span backtick or tilde, an HTML comment's '<!--', a link's '[' / ']' / '(' / ')'),
// consult isEscaped (or an equivalent forward atomic '\X'-pair skip, in a left-to-right SCANNING
// loop — see findMarkdownLinkGroups's own comment for why that style is used there instead of
// calling isEscaped directly) — an escaped delimiter never opens or closes anything; (2) DECODING
// — every target string this module hands back to a CALLER (a markdown link destination, a YAML
// scalar) has its own escape spellings REMOVED before it leaves the parse layer, because the
// caller always compares it against a filesystem-derived expectedTarget that was never escaped in
// the first place (`docs\(v2\)/x.md` in the SOURCE must resolve to `docs(v2)/x.md`, matching the
// real directory name). Decoding happens exactly ONCE, at the boundary (parseMdLinkDestination /
// decodeYamlScalar) — nothing downstream re-decodes or re-escapes.
function isEscaped(text, index) {
  let count = 0;
  let i = index - 1;
  while (i >= 0 && text[i] === '\\') {
    count += 1;
    i -= 1;
  }
  return count % 2 === 1;
}

// '\' followed by CommonMark ASCII punctuation ( !"#$%&'()*+,-./  :;<=>?@  [\]^_`  {|}~ ).
const MARKDOWN_ESCAPE_RE = /\\([\x21-\x2f\x3a-\x40\x5b-\x60\x7b-\x7e])/g;

// DECODING duty: '\' followed by an ASCII punctuation char becomes that literal char (the
// backslash is removed); '\' followed by anything else (or at end of string) stays a literal
// backslash — CommonMark's backslash-escape rule, applied once at the parse-layer boundary.
// A global regex consumes each '\X' pair atomically left-to-right — the same forward atomic
// skip as the scanning loops above, so '\\(' decodes to '\(' (the first pair wins), never '('.
function decodeMarkdownEscapes(raw) {
  return raw.replace(MARKDOWN_ESCAPE_RE, '$1');
}

// R6-F3: finds every "[label](" opener, SKIPPING duty applied to both brackets — an escaped '['
// never opens a label (isEscaped, since this is a single check at a candidate position, not a
// scanning loop); an escaped ']' inside the label never closes it (a chapter title can legitimately
// contain "[Beta]" — "- [Plans \\[Beta\\]](handbook/admin/plans.md)"). No nested-bracket support
// beyond escape-awareness — CommonMark's full label grammar allows genuine nested brackets, which
// is out of this bounded scanner's scope.
function findLinkOpeners(line) {
  const openers = [];
  let i = 0;
  while (i < line.length) {
    if (line[i] === '[' && !isEscaped(line, i)) {
      let j = i + 1;
      while (j < line.length) {
        if (line[j] === '\\') {
          j += 2;
          continue;
        }
        if (line[j] === ']') break;
        j += 1;
      }
      if (line[j] === ']' && line[j + 1] === '(') {
        openers.push(j + 2);
        i = j + 2;
        continue;
      }
    }
    i += 1;
  }
  return openers;
}

// R5-F3: a naive `[^)]+` capture for a Markdown link's parenthesized group stops at the FIRST
// ')' — but profile paths are unrestricted strings, and a legal directory segment like `docs(v2)`
// puts a literal ')' INSIDE the destination, well before the link's own closing paren. Bounded
// scanner (not a full CommonMark parser): an angle-wrapped destination (`<dest>`) consumes
// through its own matching '>' — parens inside are irrelevant there, only '<'/'>' matter; an
// unwrapped destination tracks PAREN DEPTH — an unescaped '(' increases it, an unescaped ')'
// decreases it, and the link's REAL closing paren is whichever ')' would take the depth negative
// (CommonMark's balanced-parens rule for a bare destination). R6: the '\X' forward-skip below is
// the SAME SKIPPING duty as isEscaped, spelled differently on purpose — a left-to-right scanning
// loop naturally consumes an escaping backslash and its escaped char as one atomic unit (i += 2)
// rather than checking backward at every position; the two are provably equivalent (a backslash
// run's parity is unaffected by which direction you count from) and this form avoids re-deriving
// the same parity check on every character of the loop.
export function findMarkdownLinkGroups(line) {
  const groups = [];
  for (const start of findLinkOpeners(line)) {
    let i = start;
    // An angle-wrapped destination's OWN parens never affect depth tracking (its content is
    // delimited by '<'/'>', not parens) — skip straight past the closing '>' before paren-depth
    // tracking begins, so any optional ` "Title"` that follows is scanned with fresh depth=0.
    if (line[i] === '<') {
      const gt = line.indexOf('>', i + 1);
      i = gt === -1 ? line.length : gt + 1; // past the '>' (or EOL when unterminated)
    }
    let depth = 0;
    while (i < line.length) {
      if (line[i] === '\\') {
        i += 2;
        continue;
      }
      if (line[i] === '(') {
        depth += 1;
        i += 1;
        continue;
      }
      if (line[i] === ')') {
        if (depth === 0) break;
        depth -= 1;
        i += 1;
        continue;
      }
      i += 1;
    }
    if (line[i] === ')') groups.push(line.slice(start, i));
    // Unterminated — no real closing paren was found for THIS opener; findLinkOpeners already
    // found every opener independently, so simply move on to the next one.
  }
  return groups;
}

// R4-F1/F2: the SHARED, terminal inert-context stripper — a single left-to-right pass over the
// text tracking exactly ONE inert context at a time (HTML comment, fenced code block, inline code
// span), used by BOTH locateChapterLine (R4-F2: an index line inside a comment/fence must never
// report present:true) and chapterHasWikilinkTo (R3-F3/R4-F1). Replaces the round-3 chained
// `.replace()` passes, which ran independently and were blind to what an EARLIER pass had already
// consumed — `<!-- ``` -->` followed by a rendered link and a REAL fence let the comment's
// embedded backticks pair with the real fence in the separate fenced-code pass, erasing the
// rendered link between them. INVARIANT: whichever inert construct's OPENING delimiter is reached
// FIRST in left-to-right scan order consumes to its own close (or EOF, if unterminated) as ONE
// unit; nothing inside it — including a delimiter that would otherwise start a DIFFERENT inert
// construct — is ever re-examined once consumed. First-opened wins; contexts never interleave.
// Each inert region is replaced with an equal-length, newline-preserving blank (never removed
// outright) so a construct split across a stripped boundary can never fuse into a NEW match, and
// indices into the sanitized text stay valid for isEscaped's backslash-run check. Still bounded,
// not a full CommonMark parser: fences (``` / ~~~, length >= 3) are recognized only at a line
// start (ignoring leading whitespace) — the real ATX-fence rule, which is what stops a run of 3+
// backticks mid-sentence from being mistaken for a fence; a fence's closing run must be the SAME
// character and >= the opening run's length (a 3-backtick line can never close a 4-backtick
// fence); any OTHER backtick run is an inline code span, whose closing run must match the
// opening's length EXACTLY (CommonMark's own code-span rule) and may span multiple lines.
function isLineStart(text, index) {
  let i = index - 1;
  while (i >= 0 && text[i] !== '\n') {
    if (text[i] !== ' ' && text[i] !== '\t') return false;
    i -= 1;
  }
  return true;
}

function runLength(text, index, ch) {
  let i = index;
  while (i < text.length && text[i] === ch) i += 1;
  return i - index;
}

function blankSpan(s) {
  return s.replace(/[^\n]/g, ' ');
}

// Scans forward line by line (from just past the opening fence) for a line that, after optional
// leading whitespace, starts with `ch` repeated >= openLen times. Returns the index just past that
// closing run, or the text length if no such line exists (an unterminated fence runs to EOF).
function findFenceClose(text, from, ch, openLen) {
  const n = text.length;
  let lineStart = from;
  while (true) {
    const nl = text.indexOf('\n', lineStart);
    if (nl === -1) return n;
    lineStart = nl + 1;
    let p = lineStart;
    while (p < n && (text[p] === ' ' || text[p] === '\t')) p += 1;
    if (text[p] === ch) {
      const runLen = runLength(text, p, ch);
      if (runLen >= openLen) return p + runLen;
    }
  }
}

// Scans forward (across newlines — code spans may soft-wrap) for the next backtick run whose
// length EXACTLY matches openLen. Returns the index just past it, or the text length if none
// exists (an unterminated code span runs to EOF).
function findCodeSpanClose(text, from, openLen) {
  const n = text.length;
  let i = from;
  while (i < n) {
    if (text[i] === '`') {
      const runLen = runLength(text, i, '`');
      if (runLen === openLen) return i + runLen;
      i += runLen;
    } else {
      i += 1;
    }
  }
  return n;
}

// [1.12.0] `options.indentedRunIsCode` (default false): when true, a fence-shaped run (backtick or
// tilde, length >= 3, at true line start) whose OWN column is >= 4 (tab-expanded from its line
// start — tabExpandedColumn, below) is NOT recognized as a fence opener at all — it is an indented
// CODE BLOCK, not a fence (CommonMark), and is passed through untouched (no blanking, no fallback
// to the inline-code-span path either, which carries the identical erase-to-EOF risk). Default
// false preserves every pre-1.12.0 caller's behavior byte-for-byte (locateChapterLine,
// chapterHasWikilinkTo — index-file scanning, which never exercises this column distinction); the
// image extractor (expectedAssets) is the only caller that passes `true`, because the shipped
// column-blind behavior treats a 4-space-indented backtick run as an unterminated fence and blanks
// a LIVE image after it to EOF (the "over-indented fence counterexample" the plan measures). One
// scanner, one place the fence/indented-code boundary is decided, instead of two copies that could
// silently diverge the next time either is edited.
export function stripInertContexts(text, { indentedRunIsCode = false } = {}) {
  const n = text.length;
  let out = '';
  let i = 0;

  while (i < n) {
    // R6-F1/R7: SKIPPING duty — an escaped delimiter never opens a construct, and (R7) the
    // escape applies to the delimiter's WHOLE contiguous run, not just its first character. A
    // single escaped backtick ("Type a literal \` character.") — or an escaped 2+ run ("\``",
    // "\```") — with no matching close anywhere later in the text otherwise opens an inline-code
    // span or fence that runs to EOF, silently swallowing every line after it (an index
    // reporting present:false + indexForm 'non-heading' though the real content — headings, TOC
    // rows — was still there; a chapter's removal scan false-completing because a later real
    // [[link]] got hidden the same way). See the run-atomicity comment further down.
    if (text.startsWith('<!--', i) && !isEscaped(text, i)) {
      const close = text.indexOf('-->', i + 4);
      const end = close === -1 ? n : close + 3;
      out += blankSpan(text.slice(i, end));
      i = end;
      continue;
    }

    const ch = text[i];
    if (ch === '`' || ch === '~') {
      const runLen = runLength(text, i, ch);
      // R7: the escape applies to the ENTIRE contiguous run for scanning purposes, consumed
      // ATOMICALLY as literal text — matching how the UNESCAPED case below already treats a run
      // as one atomic delimiter (openLen/runLen), never one character at a time. Checking
      // isEscaped only at position `i` and then falling through to the single-char default path
      // (`out += ch; i += 1`) copied just the run's FIRST char and left the REMAINING backticks/
      // tildes to be re-examined one position later as a fresh, UNESCAPED opener — an escaped
      // 2+ run still opened a span/fence that ran to EOF, silently hiding everything after it.
      if (isEscaped(text, i)) {
        out += text.slice(i, i + runLen);
        i += runLen;
        continue;
      }
      const atTrueLineStart = isLineStart(text, i);
      if (runLen >= 3 && atTrueLineStart) {
        if (indentedRunIsCode && tabExpandedColumn(text, lineStartOf(text, i), i) >= 4) {
          // An indented code block, not a fence — passthrough, no blanking, and no
          // inline-code-span fallback (that path has the identical erase-to-EOF risk).
          out += text.slice(i, i + runLen);
          i += runLen;
          continue;
        }
        const end = findFenceClose(text, i + runLen, ch, runLen);
        out += blankSpan(text.slice(i, end));
        i = end;
        continue;
      }
      if (ch === '`') {
        const end = findCodeSpanClose(text, i + runLen, runLen);
        out += blankSpan(text.slice(i, end));
        i = end;
        continue;
      }
    }

    out += ch;
    i += 1;
  }

  return out;
}

// A Markdown link's parenthesized group can be `dest`, `<dest with spaces>` (CommonMark's
// angle-bracket form, needed when the destination contains a space), or `dest "Title"` / `dest
// 'Title'` (an optional link title) — findMarkdownLinkGroups returns the whole raw group,
// including any wrapper/title, so the actual destination has to be pulled out of it (F5).
// R6-F2: DECODING duty — the extracted destination is escape-decoded (decodeMarkdownEscapes)
// before it is returned. `[Orders](docs\(v2\)/admin/orders.md)` in the SOURCE must resolve to
// the target `docs(v2)/admin/orders.md` — the caller's expectedTarget is always computed from
// filesystem-derived path segments (posixRelative etc.), which never contain backslash-escapes,
// so a raw (still-escaped) return here would compare unequal forever.
export function parseMdLinkDestination(raw) {
  const trimmed = raw.trim();
  if (trimmed.startsWith('<')) {
    const end = trimmed.indexOf('>');
    if (end !== -1) return decodeMarkdownEscapes(trimmed.slice(1, end));
  }
  // No angle brackets: the destination ends at the first whitespace — an optional quoted title
  // follows one. A destination with a literal space and no angle brackets is not valid CommonMark.
  return decodeMarkdownEscapes(trimmed.split(/\s/, 1)[0]);
}

// F3: a YAML scalar value may be quoted ('...'/"...") and/or carry a trailing end-of-line `#
// comment`. Not a full YAML scanner — just enough to keep the two shapes the finding names from
// halting forever: `- Items: "handbook/admin/items.md"` (quoted) and `- Items:
// handbook/admin/items.md # grouped` (trailing comment). A quoted value's own closing quote wins
// over comment detection (anything after it, `#` or not, is discarded — YAML allows nothing else
// there for a scalar); an unquoted value's comment starts at a `#` preceded by whitespace.
//
// R6 DECODING sweep decision: YAML double-quoted scalars support C-style backslash escapes;
// single-quoted scalars do NOT (their only escape is a DOUBLED quote '' -> ', a delimiter-search
// concern, not a value-decoding one, and untouched here) — plain/unquoted scalars have no
// escaping at all. Only the two escapes plausible in a path/label value are decoded for
// double-quoted strings: \" (literal quote) and \\ (literal backslash); full YAML escape support
// (\n, \t, \uXXXX, ...) is out of scope — chapter/group labels and paths are never free text
// needing whitespace/control-char escapes. NOTE (documented limitation, not fixed): the closing-
// quote SEARCH itself is not escape-aware — a double-quoted value containing an escaped `\"`
// would still end the scalar early at that position. Real chapter-nav labels are simple paths or
// short titles that don't embed quotes; escape-aware quote-matching would be actual YAML string
// parsing, beyond this bounded scanner's scope. A YAML PLAIN scalar's '\: ' (escaped colon-space)
// is likewise out of scope: a literal ': ' inside an UNQUOTED YAML key is not valid YAML at all
// without quoting the whole key, which this module never needs to support for a label/slug.
const YAML_DOUBLE_QUOTE_ESCAPE_RE = /\\(["\\])/g;

function decodeYamlScalar(raw) {
  const trimmed = raw.trim();
  if (trimmed.length >= 2 && trimmed[0] === '"') {
    const end = trimmed.indexOf('"', 1);
    if (end !== -1) return trimmed.slice(1, end).replace(YAML_DOUBLE_QUOTE_ESCAPE_RE, '$1');
  }
  if (trimmed.length >= 2 && trimmed[0] === "'") {
    const end = trimmed.indexOf("'", 1);
    if (end !== -1) return trimmed.slice(1, end);
  }
  return trimmed.split(/\s#/, 1)[0].trim();
}

function extractLineTargets(line) {
  // R3-F2(a): a line whose first non-whitespace character is '#' is a heading (already handled
  // by the caller's HEADING_RE check for the unindented, column-0 case) OR a YAML end-of-line
  // comment, indented or not (`  # - Items: handbook/admin/items.md` — a commented-out nav row).
  // Neither is ever a real TOC entry; without this, the bare-scalar fallback below would strip
  // the leading '#' via no rule at all and happily extract the commented-out row's target,
  // reporting present:true for wiring that never actually happened.
  if (line.trimStart().startsWith('#')) return [];
  const mdTargets = findMarkdownLinkGroups(line).map(parseMdLinkDestination);
  // R6 sweep decision: wikilink targets are always slugs or chapters_dir-relative paths — never
  // free text containing a literal ']]' — so only the OPENER needs SKIPPING (an escaped '[[' is
  // documentation about the syntax, not a real link); a decoding duty for the CAPTURED target
  // is not needed, since a real target can never itself contain an escape sequence.
  const wikiTargets = [...line.matchAll(WIKILINK_TARGET_RE)]
    .filter((m) => !isEscaped(line, m.index))
    .map((m) => m[1]);
  const targets = [...mdTargets, ...wikiTargets];
  // A bare YAML nav entry — only when the line carries no markdown-link/wikilink syntax, so
  // ordinary prose is never mistaken for a link target. Two shapes: an unlabeled sequence scalar
  // (`- handbook/admin/items.md`) and MkDocs' canonical LABELED row (`- Items:
  // handbook/admin/items.md`, a YAML mapping) — the target is whatever follows the first ": "
  // once the leading list marker is stripped; an unlabeled scalar has no ": " and is used as-is.
  // Either shape's value is then YAML-scalar-decoded (F3, above).
  if (targets.length === 0) {
    const stripped = line.trim().replace(/^-\s*/, '');
    if (stripped) {
      const labelSep = stripped.indexOf(': ');
      const rawValue = labelSep === -1 ? stripped : stripped.slice(labelSep + 2);
      targets.push(decodeYamlScalar(rawValue));
    }
  }
  return targets;
}

// R3-F2(b): depth >= 2 alone is NOT sound evidence of headings-form — a YAML comment can itself
// be spelled with two hashes (`## Secondary navigation` inside mkdocs.yml), defeating the depth
// heuristic on its own. INVARIANT: no #-shaped line can EVER, by itself, prove headings form;
// only the ABSENCE of non-markdown (YAML-mapping) structure, combined with depth >= 2 headings,
// can. This scans for `key:` / `- key: value` mapping-shaped lines — the two forms mkdocs.yml's
// `nav:` block and MkDocs' own labeled-row TOC entries both use, and no genuine Obsidian INDEX.md
// section body would produce — OUTSIDE a leading YAML frontmatter block, which is deliberately
// exempted: it is the one place a shipped headings-form Obsidian index legitimately carries real
// `key: value` lines (`type: handbook`, `status: active`, …) ahead of its `##` containers.
const YAML_MAPPING_LINE_RE = /^[A-Za-z0-9_.-]+:(\s|$)/;

function hasYamlMappingStructure(sanitizedLines) {
  let i = 0;
  while (i < sanitizedLines.length && sanitizedLines[i].trim() === '') i += 1;
  if (i < sanitizedLines.length && sanitizedLines[i].trim() === '---') {
    // R4-F3: only skip the block when a genuine CLOSING '---' is actually found. Unconditionally
    // advancing past it (the round-3 bug) sent `i` past the end of the array once no closer
    // existed, so `.slice(i)` silently returned [] and the structural check never ran on the rest
    // of the document — a lone leading '---' with no close is a plain YAML document-start marker,
    // not frontmatter, and must NOT exempt anything.
    let j = i + 1;
    while (j < sanitizedLines.length && sanitizedLines[j].trim() !== '---') j += 1;
    if (j < sanitizedLines.length) i = j + 1;
  }
  return sanitizedLines
    .slice(i)
    .some((line) => YAML_MAPPING_LINE_RE.test(line.trim().replace(/^-\s*/, '')));
}

// Every depth >= 2 heading in the (already sanitized) lines, with its ORIGINAL array index — a
// depth-1 line is never a container (D6 convention: containers are always `##`, see
// locateChapterLine's own containerTitle comment).
function collectContainerHeadings(sanitizedLines) {
  const headings = [];
  sanitizedLines.forEach((line, index) => {
    const m = line.match(HEADING_RE);
    if (m && m[1].length >= 2) headings.push({ index, depth: m[1].length, title: m[2].trim() });
  });
  return headings;
}

// R5-F1/F2: the SHARED headings-form classifier — every caller keys on the EXACT same logic, over
// the EXACT same sanitized view (today: `locateChapterLine`'s `indexForm` field and
// `findContainer`'s non-heading branch), so no two callers, however many there are, can ever
// disagree about what kind of file they're looking at.
// Headings-form iff the sanitized text has at least one depth >= 2 heading AND no YAML-mapping
// structure outside frontmatter (R3-F2(b)) — an inert `## Secondary navigation` inside a YAML
// comment or fenced block never counts either way, since it was already blanked before this runs.
function classifyIndexForm(sanitizedLines) {
  if (hasYamlMappingStructure(sanitizedLines)) return 'non-heading';
  return collectContainerHeadings(sanitizedLines).length > 0 ? 'headings' : 'non-heading';
}

// D6 (opt-in, {wikilink:true} only): folds ONE terminal '.md' off a normalized target, ASCII
// case-insensitively — the same Obsidian `[[note.md]] == [[note]]` equivalence
// parseWikilinkTarget already applies for the removal-scan predicate. Default (`wikilink: false`)
// leaves the normalized target untouched, so path-mode targets (which legitimately END in `.md`)
// and every pre-1.8.0 caller stay byte-for-byte identical.
//
// #311: that path-mode byte-identity is INTENTIONAL, not an oversight — the fold must NOT be
// generalized to path mode. In path mode a target is a real filesystem href where the '.md' is
// load-bearing: `items` and `items.md` are DIFFERENT resources (one 404s, the other serves), and
// there is no Obsidian `[[note.md]] == [[note]]` equivalence off the static site — so folding here
// would manufacture a FALSE-POSITIVE match against a divergent href. A stale or divergent
// hand-authored path-mode line is therefore left UNMATCHED: static-md's step-0 flat-entry-absent
// branch then appends the canonical `.md` row and RETAINS the divergent row alongside it
// (append-and-retain) — a benign redundant entry, not a silent false-match. The link-integrity
// gate does NOT reject the retained row (item 5 only needs ONE resolving index link; item 2
// checks the CHAPTER's own links, not an index-wide sweep) — removing a stale alias row would
// need an index-wide broken-link/alias check (a possible future improvement, out of scope here).
function foldTargetForMatch(target, wikilink) {
  const normalized = normalizeLinkTarget(target);
  return wikilink ? normalized.replace(/\.md$/i, '') : normalized;
}

/**
 * Step-0 idempotency check (D6). Scans `indexLines` for any line whose extracted target
 * (markdown-link href, wikilink target, or bare path) normalize-equals `expectedTarget` — the
 * CALLER computes and resolves expectedTarget (relative(dirname(index_file), chapter_file) for
 * path links, or the vault-root-relative chapter path — currentIndexExpectedTarget's wikilinks
 * branch, §1a — for wikilink lines; see manifest-discipline's coordinate system). `containerTitle`
 * is the nearest PRECEDING markdown heading (null outside any heading — the non-heading-form
 * case, OR — R5-F1 — an active line before any container / after a depth-1 heading RESET in a
 * HEADINGS-form file: `containerTitle: null` is ambiguous between those two shapes on its own,
 * which is exactly what `indexForm` disambiguates. `indexForm: 'headings'` + `containerTitle:
 * null` means UNCONTAINED — a real line sitting outside any `##` section in a genuine
 * headings-form index (the caller halts wrong-placement via `containerTitleMatches` returning
 * false, same as any other container mismatch); `indexForm: 'non-heading'` + `containerTitle:
 * null` is the ordinary non-heading-form membership-only case. Every occurrence is collected in
 * `matches` so callers can run the old-container wikilink proof (D6) over every hit, not just the
 * first.
 *
 * `options.wikilink` (D6, default `false`): when `true`, folds ONE terminal `.md`
 * (case-insensitive) off both `expectedTarget` and every extracted line target before comparison
 * (`foldTargetForMatch`) — so a user-authored `[[handbook/orders.md]]` / `[[orders.md]]` row is
 * recognised as the same target as `handbook/orders` / `orders`, never double-appended. Default
 * `false` keeps path-mode and every existing caller byte-for-byte unchanged (a path-link target
 * legitimately ends in `.md` and must never be folded — #311: this byte-identity is intentional;
 * a divergent path-mode line stays unmatched, so step 0 appends the canonical row and RETAINS the
 * divergent one alongside it (append-and-retain) — the link-integrity gate does not reject it).
 *
 * The sanitized view `locateChapterLine` scans — name-the-expression pattern (§5, 1.11.0):
 * extracted verbatim from `locateChapterLine` below, so the one expression has exactly one
 * implementation and is directly unit-testable in isolation. That is what this export buys, stated
 * as an invariant rather than a caller count (round-3 review: a prior wording named
 * `locateChapterLine` as the "only" caller, which a later, unrelated fix to a second function made
 * false): whatever needs this sanitized view reaches it by calling this function, never by
 * re-deriving the expression inline — true regardless of how many callers exist or which functions
 * they are. (The extraction itself changed nothing; `locateChapterLine`'s return shape later gained
 * `index` on each match record, #330 round-2 review — additive, not "no change": the `.d.mts`
 * publishes the field. Nothing broke because no consumer treats the record as a CLOSED shape —
 * every one reads the fields it needs and ignores the rest, so an additive field is invisible to
 * all of them. That is the durable statement; the enumeration this sentence used to carry ("reads
 * `.length`, filters on `.containerTitle`, or reads `.index`") was already incomplete when written,
 * since `obsidian-vault.md` reads `matches[0].line`. Note it was itself a correction of an earlier
 * stale claim: a fix can be born stale, so a consumer census is worth no more than a caller one.)
 * The present-line placement verifier (`verifyNonHeadingPlacement`, #330) reaches the same view
 * transitively, by delegating to `locateChapterLine` itself for its match indices, not by calling
 * this export directly — an earlier revision did call it directly and re-implemented
 * `locateChapterLine`'s match loop alongside it, which review caught as the second recognizer this
 * pattern exists to prevent.
 *
 * @param {string[]} indexLines
 * @returns {string[]}
 */
export function indexView(indexLines) {
  // R4-F2: sanitize the WHOLE index text (not line-by-line — an inert region can itself span
  // multiple lines) through the shared stripper BEFORE any per-line processing, so a row sitting
  // inside an HTML comment or a fenced code block can never report present:true (a false
  // completion — the wiring is declared done when it never actually happened). join/split on '\n'
  // round-trips exactly because stripInertContexts preserves every newline unmodified.
  return stripInertContexts(indexLines.join('\n')).split('\n');
}

/**
 * @param {string[]} indexLines
 * @param {string} expectedTarget
 * @param {{wikilink?: boolean}} [options]
 * @returns {{present: boolean, containerTitle: string|null, multiple: boolean, indexForm: 'headings'|'non-heading', matches: Array<{index: number, line: string, containerTitle: string|null}>}}
 */
export function locateChapterLine(indexLines, expectedTarget, options = {}) {
  const { wikilink = false } = options;
  const wanted = foldTargetForMatch(expectedTarget, wikilink);
  const sanitizedLines = indexView(indexLines);
  const indexForm = classifyIndexForm(sanitizedLines);
  const matches = [];
  let containerTitle = null;

  for (const [index, line] of sanitizedLines.entries()) {
    const heading = line.match(HEADING_RE);
    if (heading) {
      // F1: only a depth >= 2 heading anchors a container. A depth-1 line is either the
      // document's own title (never a group container by D6 convention — containers are `##`)
      // or, just as plausibly, a YAML end-of-line comment (`# Main navigation` in mkdocs.yml) —
      // this function has no way to know the file's real format from a bare line array, so it
      // must not let a lone depth-1 `#`-line become a spurious containerTitle (closing the
      // wrong-container false-halt risk). R3-F2(c): a depth-1 heading RESETS the current
      // container to null instead of leaving the prior depth>=2 title in effect — outline
      // semantics: an H1 (`# Appendix`) ends whatever `##` section preceded it, so a TOC line
      // sitting after it is no longer "under" that earlier container. Only a depth>=2 heading
      // both sets AND (implicitly, via the next depth-1) clears containerTitle.
      containerTitle = heading[1].length >= 2 ? heading[2].trim() : null;
      continue;
    }
    const targets = extractLineTargets(line);
    if (targets.some((t) => foldTargetForMatch(t, wikilink) === wanted)) {
      // Report the ORIGINAL (unsanitized) line text — `matches[].line` is diagnostic/halt
      // output, and a reader must see the real file content, never a blanked stand-in. `index`
      // is this match's position into `indexView(indexLines)` (1.11.0 #330 review fix): the
      // present-line placement verifier needs a line INDEX (for its frontmatter-span check and
      // its container-walk lookup) that this return shape did not carry before — adding it here
      // let the verifier delegate to this loop instead of running a second, parallel one.
      matches.push({ index, line: indexLines[index], containerTitle });
    }
  }

  return {
    present: matches.length > 0,
    containerTitle: matches[0]?.containerTitle ?? null,
    multiple: matches.length > 1,
    indexForm,
    matches,
  };
}

/**
 * classifyChapterWiring(qualifiedTarget, legacyBareTarget, qScan, lScan) — D7: the single
 * union-count algorithm the vault-rel legacy-transition Step-0 idempotency check (§1b) drives at
 * W5. Pure over the two target STRINGS plus the two `locateChapterLine` results the caller already
 * computed (`qScan = locateChapterLine(lines, qualifiedTarget, {wikilink:true})`, `lScan =
 * locateChapterLine(lines, legacyBareTarget, {wikilink:true})`) — it never re-scans the index
 * itself, so it is directly unit-testable in isolation.
 *
 * Dedup guard (root-topology flat case, codex R3 BLOCKER): when
 * `normalizeLinkTarget(qualifiedTarget) === normalizeLinkTarget(legacyBareTarget)` the two scans
 * searched the IDENTICAL string (`vaultRelChaptersDir === ''`, no group ⇒ qualified === legacyBare
 * === slug — §0a's "SAFE, no halt" root topology) — counting both would double-count every
 * correctly-wired line into a false `'duplicate'`. So `count = qScan.matches.length + (same ? 0 :
 * lScan.matches.length)`.
 *
 * Returns one of:
 * - `'absent'`    — `count === 0`: no line wires this chapter yet (caller appends).
 * - `'duplicate'` — `count > 1`: ambiguous (manual halt). A single row that carries BOTH a
 *   qualified and a distinct legacy link (`!same`) is deliberately classified here too — a
 *   malformed double-reference row is a safe halt, not silent wiring.
 * - `'canonical'` — otherwise, when `qScan.matches.length === 1` (the qualified form is present).
 * - `'legacy'`    — otherwise (the single match is the legacy bare form only).
 *
 * D8: this function answers target-string PRESENCE + FORM ONLY — it says nothing about
 * PLACEMENT. The existing container-placement halts (a correctly-spelled line under the wrong
 * `##` heading, or an uncontained match in a headings-form index) are a SEPARATE gate the caller
 * still runs over `qScan.matches[].containerTitle` (`containerTitleMatches`) — layered on top of,
 * never replaced by, a `'canonical'`/`'legacy'` outcome.
 *
 * @param {string} qualifiedTarget
 * @param {string} legacyBareTarget
 * @param {{matches: Array<{line: string, containerTitle: string|null}>}} qScan
 * @param {{matches: Array<{line: string, containerTitle: string|null}>}} lScan
 * @returns {'absent'|'canonical'|'legacy'|'duplicate'}
 */
export function classifyChapterWiring(qualifiedTarget, legacyBareTarget, qScan, lScan) {
  const same = normalizeLinkTarget(qualifiedTarget) === normalizeLinkTarget(legacyBareTarget);
  const count = qScan.matches.length + (same ? 0 : lScan.matches.length);
  if (count === 0) return 'absent';
  if (count > 1) return 'duplicate';
  return qScan.matches.length === 1 ? 'canonical' : 'legacy';
}

/**
 * Container resolution (D6), reached only when step 0 (locateChapterLine) found no existing
 * line. R5-F2: runs on the SAME sanitized view locateChapterLine uses (shared
 * `stripInertContexts` + `classifyIndexForm`) — an index line/heading sitting inside an HTML
 * comment or fenced block (`<!-- ## Admin -->`) must never be treated as a real container;
 * `location.index` still refers to the ORIGINAL `indexLines` array (sanitization is newline-
 * preserving and 1:1, so the index is valid either way — same pattern as `matches[].line`).
 * Headings-only contract: a file with no markdown heading at depth >= 2, OR one that contains
 * YAML-mapping-shaped structure anywhere outside a leading frontmatter block
 * (`hasYamlMappingStructure`, R3-F2(b) — depth >= 2 alone is not sound evidence; see its own
 * comment), is classified `'non-heading'`. findContainer itself is UNCHANGED — it still returns
 * `{kind:'non-heading'}` for every such file; but [#223, 1.10.0] that verdict is no longer the end
 * of the road: the adapter now falls through to `wireNestedListChapter` (below), which auto-wires a
 * BOUNDED nested-list safe subset (§5.1 — plain-label GitBook `SUMMARY.md` lists) and defers
 * everything outside it (YAML `nav:`, bare path tables, exotic labels) to the existing manual halt
 * via `{kind:'not-a-list'}`. Depth >= 2 is deliberate (F1): a GROUP CONTAINER is `##
 * <group_title>` by D6 convention, never a bare `#`, so requiring depth >= 2 as evidence closes
 * two false-positive classifications a naive "any `#`-line" detector hits — a YAML end-of-line
 * comment (`# Main navigation` in mkdocs.yml) and a GitBook `SUMMARY.md` that opens with a single
 * `# Summary` document title followed by nested lists (no real heading-based containers at all)
 * — both are depth-1-only and correctly stay non-heading, never silently classified as
 * headings-form on the strength of one comment/title line. Within a headings-form file: zero
 * matching (depth >= 2) headings ⇒ `'zero'` (create, at the depth of an EXISTING depth >= 2
 * heading — never a depth-1 document title, which this function excludes from `headings`
 * entirely); exactly one ⇒ `'single'` (append under it); more than one ⇒ `'multiple'`
 * (container-ambiguous halt). `groupTitle` is trimmed before comparison (F5) so a padded value
 * still converges against the (already-trimmed) heading text.
 *
 * @param {string[]} indexLines
 * @param {string} groupTitle
 * @returns {{kind: 'zero', headingDepth: number}
 *         | {kind: 'single', location: {index: number, depth: number, title: string}}
 *         | {kind: 'multiple', matches: Array<{index: number, depth: number, title: string}>}
 *         | {kind: 'non-heading'}}
 */
export function findContainer(indexLines, groupTitle) {
  const wanted = containerLabelKey(groupTitle);
  const sanitizedLines = indexView(indexLines);
  if (classifyIndexForm(sanitizedLines) === 'non-heading') return { kind: 'non-heading' };
  const headings = collectContainerHeadings(sanitizedLines);

  const matches = headings.filter((h) => h.title === wanted);
  if (matches.length > 1) return { kind: 'multiple', matches };
  if (matches.length === 1) return { kind: 'single', location: matches[0] };
  return { kind: 'zero', headingDepth: headings[0].depth };
}

// ---------------------------------------------------------------------------------------------
// #223 [1.10.0] — nested-list (GitBook `SUMMARY.md`) grouped-index WRITE automation
//
// Reached only when findContainer(...) returned {kind:'non-heading'} AND step 0
// (locateChapterLine) found no existing chapter line — the adapter falls through to
// wireNestedListChapter, which either fully mutates a BOUNDED, plain-label nested-list index or
// declines with {kind:'not-a-list'} (the caller then keeps today's manual halt, byte-identical).
// The whole soundness argument (§5.1/§5.4/§5.5 of the plan) rests on a single invariant: after the
// frontmatter block is blanked and the inert-identity guard has refused any file carrying a
// comment/fence/inline-code span, EVERY non-frontmatter BODY line is byte-identical to its raw
// form — so the line we classify IS the line we edit and the label we match IS its rendered label,
// and a positive plain-label allowlist (isPlainLabel) keeps every container label and the emitted
// group_title trivially plain so literal equality equals rendered equality. Everything ambiguous
// fails toward manual.
// ---------------------------------------------------------------------------------------------

// §5.1/§5.2 EXACT shapes (pinned by the shared contract): a bullet is spaces-only indent, one
// marker char, exactly ONE ASCII space, then a first content char that is neither space nor tab
// (the `(?![ \t])` lookahead — R6-2 — so `-   Admin`, `-\tAdmin`, `- \tAdmin` and a lone `-` all
// fall through to FOREIGN). A thematic break is tested against the line's `.trim()` (R6-3) so a
// `<hr>` at ANY indent (`    - - -`) is excluded before the bullet branch, since `- - -` / `* * *`
// also match the bullet regex.
const NESTED_BULLET_RE = /^( *)([-*+]) (?![ \t])(.*)$/;
const NESTED_THEMATIC_BREAK_RE = /^([-*_])([ \t]*\1){2,}$/;
const NESTED_ATX_HEADING_RE = /^#{1,6}\s/;
const NESTED_ORDERED_MARKER_RE = /^\s*\d+[.)]\s/;

/**
 * §5.2 escape-aware whole-content label extraction, returning both the display LABEL and the shape
 * KIND ('mdlink' | 'wikilink' | 'raw') the §5.7 bare-path guard keys on. Private core of the
 * exported extractLabel (which returns only `.label`).
 *
 * On `t = content.trim()`:
 * - Whole-content markdown link — `t` is exactly one `[label](dest)` spanning the entire string:
 *   the SAME escape-aware scan as findLinkOpeners/findMarkdownLinkGroups (from a `[` at position 0
 *   skip `\X` to the closing `]`, then `(`, an optional `<…>` angle-wrapped destination, balanced
 *   `)`, then only optional whitespace to end) ⇒ decodeMarkdownEscapes(labelText), kind 'mdlink'.
 *   Rejects `See [Admin](a.md)` (`[` not at 0) and `[A](a) [B](b)` (does not end at the first
 *   real `)`), both of which fall to 'raw'.
 * - Whole-content wikilink — `^\[\[([^\]]*)\]\]$`: the alias after `|` if present, else the target
 *   before `#`/`^`; kind 'wikilink'.
 * - Otherwise ⇒ the trimmed content verbatim, kind 'raw'.
 *
 * Deliberately NOT findMarkdownLinkGroups (that returns the DESTINATION, chapter-paths.mjs:562-596).
 */
function parseNestedLabel(content) {
  const t = String(content).trim();

  if (t[0] === '[') {
    // Closing ']' of the label, escape-aware (an escaped `\]` never closes — same left-to-right
    // atomic '\X' skip findLinkOpeners uses for a legitimate "[Plans \[Beta\]]" title).
    let j = 1;
    while (j < t.length) {
      if (t[j] === '\\') {
        j += 2;
        continue;
      }
      if (t[j] === ']') break;
      j += 1;
    }
    if (t[j] === ']' && t[j + 1] === '(') {
      let i = j + 2;
      // An angle-wrapped destination's own parens never affect depth — skip past its '>' first
      // (mirrors findMarkdownLinkGroups:569-572).
      if (t[i] === '<') {
        const gt = t.indexOf('>', i + 1);
        i = gt === -1 ? t.length : gt + 1;
      }
      let depth = 0;
      let closeParen = -1;
      while (i < t.length) {
        if (t[i] === '\\') {
          i += 2;
          continue;
        }
        if (t[i] === '(') {
          depth += 1;
          i += 1;
          continue;
        }
        if (t[i] === ')') {
          if (depth === 0) {
            closeParen = i;
            break;
          }
          depth -= 1;
          i += 1;
          continue;
        }
        i += 1;
      }
      // Whole-content only: nothing but optional whitespace may follow the link's own closing ')'.
      if (closeParen !== -1 && t.slice(closeParen + 1).trim() === '') {
        return { label: decodeMarkdownEscapes(t.slice(1, j)), kind: 'mdlink' };
      }
    }
  }

  const wiki = t.match(/^\[\[([^\]]*)\]\]$/);
  if (wiki) {
    const inner = wiki[1];
    const pipe = inner.indexOf('|');
    if (pipe !== -1) return { label: inner.slice(pipe + 1).trim(), kind: 'wikilink' };
    return { label: inner.split(/[#^]/, 1)[0].trim(), kind: 'wikilink' };
  }

  return { label: t, kind: 'raw' };
}

/**
 * §5.2 — the display text a nested-list bullet's `content` renders to, matched by exact equality
 * against the trimmed group_title (and emitted verbatim as a container label on create). Escape-
 * aware whole-content link/wikilink unwrap, else the trimmed content verbatim. Exported so §9's
 * direct unit tests can import it (R5-4).
 *
 * @param {string} content  a bullet's post-marker content (already single-space-separated)
 * @returns {string}
 */
export function extractLabel(content) {
  return parseNestedLabel(content).label;
}

/**
 * §5.1 plain-label allowlist (R5-1→R6-1) — the positive whitelist that replaced the receding
 * markup denylist. `s` is ALREADY TRIMMED by the caller. A label/title is "plain" (its rendered
 * form equals its literal form, so a literal match equals a rendered match) iff ALL hold:
 * - no inline-active char: none of `` \ * _ < > & ~ [ ] ! ` `` (the backtick is included here
 *   because it opens a code span in RENDERED markdown — the inert-identity guard upstream only
 *   refuses a backtick already present in the INDEX FILE body via stripInertContexts, and has no
 *   reach over a manifest-supplied group_title, which never passes through that sanitizer; this
 *   allowlist is what refuses a backtick- (or `<`/`~`-) bearing group_title on ITS side, R2);
 * - no leading block trigger: not a leading ATX heading (`# `), not a leading list marker
 *   (`- `/`+ `/`1. `/`1) `; `*`/`>` already caught by the inline-active char rule);
 * - no whitespace-collapse or tab (HTML folds a run of spaces/tabs to one, so `A  B` and `A B`
 *   would render-collide though their source differs).
 * Ordinary `.`, interior `-`/`+`, `(`, `)`, `/`, `:`, single interior spaces, letters and digits
 * stay allowed — they render literally in label position.
 *
 * @param {string} s  an already-trimmed candidate label / group_title
 * @returns {boolean}
 */
export function isPlainLabel(s) {
  if (/[\\*_<>&~[\]!`]/.test(s)) return false;
  if (/^#{1,6}(\s|$)/.test(s)) return false;
  if (/^([-+]|\d+[.)])(\s|$)/.test(s)) return false;
  if (/[ \t]{2,}/.test(s)) return false;
  if (/\t/.test(s)) return false;
  return true;
}

// §5.7 bare-path guard: step-0's bare-row fallback strips only `-` (chapter-paths.mjs:812), so a
// `*`/`+`-marked bullet whose content is a bare (non-link) path is INVISIBLE to the caller's
// membership scan — auto-wiring would create a duplicate container beside the retained phantom
// text row. Refuse when the marker is `*`/`+`, the label fell to the RAW branch (not a whole-
// content link/wikilink), and the raw value contains `/` OR `\` (the path layer treats `\`≡`/`,
// chapter-paths.mjs:46-49) OR ends in `.md` (case-insensitive). Deliberately conservative — a
// legitimate `*`/`+` plain label containing `/` (`* Sales/Marketing`) is also refused (defers to
// manual, never corrupts). `-`-marked such rows ARE seen by step 0, so they stay automatable.
function isBarePathBullet(marker, info) {
  if (marker !== '*' && marker !== '+') return false;
  if (info.kind !== 'raw') return false;
  return info.label.includes('/') || info.label.includes('\\') || /\.md$/i.test(info.label);
}

/**
 * §5.1 steps 1-3 and 5-6 (1.11.0 #330 extraction): the writer's own line-preparation pass, lifted
 * out of wireNestedListChapter so the present-line placement verifier can share it rather than
 * re-implement it — a second recognizer is exactly the drift the delegation design exists to
 * prevent. PRIVATE — not exported, so it stays free to change; the writer consumes every
 * emission-relevant field below and ignores `span`. `leadingFrontmatterSpan` below is this call's
 * one designated public window — by design kept to `{kind, span}` and reached by tests alone, so
 * this function's other fields never become a compatibility obligation.
 *
 * Deliberately does NOT run step 4 (the groupTitle/chapterLink embedded-newline guard) — that
 * guard reads arguments this helper never receives, so it stays in the writer.
 *
 * @param {string[]} indexLines
 * @returns {{kind: 'not-a-list'}
 *         | {kind: 'ok', logical: string[], eol: '\n'|'\r\n', hadTerminalNewline: boolean,
 *            span: {start: 0, endExclusive: number} | null, body: string[]}}
 */
function prepareIndexLines(indexLines) {
  // §5.1 step 1-3: undo the runtime split, detect the EOL, and split logically on it.
  const original = indexLines.join('\n');
  // A lone '\r' not part of a '\r\n' pair (old-Mac EOL, or a stray '\r') ⇒ not a list.
  if (/\r(?!\n)/.test(original)) return { kind: 'not-a-list' };
  const isCRLF = original.includes('\r\n');
  // Mixed EOL: after removing every CRLF, a surviving '\n' means bare-LF lines coexist with CRLF.
  if (isCRLF && original.replace(/\r\n/g, '').includes('\n')) return { kind: 'not-a-list' };
  const eol = isCRLF ? '\r\n' : '\n';

  const rawLines = original.split(eol);
  const hadTerminalNewline = rawLines.length > 0 && rawLines[rawLines.length - 1] === '';
  // The content lines, guaranteed '\r'-free (split on the detected EOL). Never mutated — every
  // emission below is built from fresh slice/concat arrays, so indexLines is never touched.
  const logical = hadTerminalNewline ? rawLines.slice(0, -1) : rawLines;

  // §5.1 step 5: blank a leading frontmatter block, with a robust column-0 closer (an EXACT,
  // untrimmed '---'/'...' — an indented '  ---' inside a block scalar is scalar content, NOT the
  // closer, so the module's own `.trim()==='---'` test at :843 is deliberately NOT reused). Blanking
  // here, BEFORE the sanitizer, also stops a backtick inside YAML scalar content from being misread
  // by stripInertContexts (which has no frontmatter awareness, :675-731).
  let fm = logical;
  let span = null;
  if (logical[0] === '---') {
    let j = 1;
    while (j < logical.length && logical[j] !== '---' && logical[j] !== '...') j += 1;
    if (j >= logical.length) return { kind: 'not-a-list' }; // unclosed frontmatter
    fm = logical.slice();
    for (let x = 0; x <= j; x += 1) fm[x] = '';
    span = { start: 0, endExclusive: j + 1 };
  }

  // §5.1 step 6 — the load-bearing R3 fix: refuse any file carrying an HTML comment / fenced block /
  // inline-code span. stripInertContexts is newline-preserving and 1:1 (:610), so SAN[i] === fm[i]
  // for every line holds EXACTLY when no such inert construct exists — making every non-frontmatter
  // BODY line byte-identical to its raw form (no sanitized-vs-raw gap on any line we classify or
  // edit) and keeping our view consistent with the caller's step-0 scan, which also sanitizes.
  const SAN = stripInertContexts(fm.join('\n')).split('\n');
  for (let i = 0; i < fm.length; i += 1) {
    if (SAN[i] !== fm[i]) return { kind: 'not-a-list' };
  }

  return { kind: 'ok', logical, eol, hadTerminalNewline, span, body: fm };
}

/**
 * The exported NARROW projection of prepareIndexLines — `{kind, span}` only, so it stays a test
 * seam without publishing `logical`/`eol`/`hadTerminalNewline` (the writer's emission internals) as
 * a compatibility obligation. Reached by tests alone: every production caller needing this
 * preparation state calls the private `prepareIndexLines` directly instead, whoever they are —
 * this narrow shape deliberately withholds the fields their emission logic needs.
 *
 * @param {string[]} indexLines
 * @returns {{kind: 'not-a-list'} | {kind: 'ok', span: {start: 0, endExclusive: number} | null}}
 */
export function leadingFrontmatterSpan(indexLines) {
  const prep = prepareIndexLines(indexLines);
  if (prep.kind === 'not-a-list') return { kind: 'not-a-list' };
  return { kind: 'ok', span: prep.span };
}

/**
 * §5.1 single forward pass over BODY (1.11.0 #330 extraction): the writer's own container-
 * resolution scan, lifted UNCHANGED out of wireNestedListChapter — including its `!sawTop`
 * conclusion and the `childIndent` normalization, so the helper produces its own complete,
 * declared record rather than a partial one the caller must finish. PRIVATE: there must be
 * exactly ONE implementation of this scan, so the #330 verifier shares it rather than
 * re-implementing the `currentContainer` loop (scan-logic drift is exactly the risk sharing only
 * the prepared BODY array would leave open).
 *
 * Total over the forward pass's OWN rejections only — not the pre-loop BODY guards
 * (hasYamlMappingStructure, isPlainLabel(wanted)), which the caller has already run by the time
 * this is called.
 *
 * `ownerOf`/`ownerLabelOf` are new: `ownerOf[i]` is the owning container's BODY index when line
 * `i` is a child bullet, `-1` when line `i` is itself an indent-0 bullet (a container is not its
 * own child), and unset for any other line (blank, or already refused above this call). Every
 * owner is recorded with its container's own UNTRIMMED parsed label — never a re-derivation —
 * because `containers` records only indent-0 bullets whose label equals `wanted`, so a mismatched
 * container's own label would otherwise be lost (round-22 HIGH).
 *
 * @param {string[]} body
 * @param {string} wanted  the trimmed group_title the caller is resolving a container for
 * @returns {{kind: 'not-a-list'}
 *         | {kind: 'ok', containers: Array<{index:number, label:string, marker:string}>,
 *            childIndent: number, firstTopMarker: string|null, lastBulletIndex: number,
 *            ownerOf: Array<number|undefined>, ownerLabelOf: Array<string|undefined>}}
 */
function containerOwnerScan(body, wanted) {
  let sawTop = false;
  let currentContainer = null; // index of the last indent-0 bullet; reset by any heading
  let currentContainerLabel = null; // that bullet's own untrimmed parsed label
  let childIndentSeen = null; // C: the file's single child indent, if any child bullet exists
  let firstTopMarker = null; // marker of the FIRST indent-0 bullet (used on ZERO create)
  let lastBulletIndex = -1; // greatest index that is any bullet (indent 0 or child)
  const containers = []; // indent-0 bullets whose extracted label === wanted
  const ownerOf = new Array(body.length);
  const ownerLabelOf = new Array(body.length);

  for (let i = 0; i < body.length; i += 1) {
    const line = body[i];
    if (line.trim() === '') continue; // blank line — tolerated inside/between regions

    // 1. ATX heading — allowed; ends any open list region.
    if (NESTED_ATX_HEADING_RE.test(line)) {
      currentContainer = null;
      currentContainerLabel = null;
      continue;
    }
    // 2. Thematic break at ANY indent (on the trimmed line) — before the bullet branch, because
    //    `- - -` / `* * *` also match the bullet regex but are horizontal rules, not list parents.
    if (NESTED_THEMATIC_BREAK_RE.test(line.trim())) return { kind: 'not-a-list' };
    // 3. Ordered-list marker.
    if (NESTED_ORDERED_MARKER_RE.test(line)) return { kind: 'not-a-list' };

    // 4/5. Bullet.
    const m = line.match(NESTED_BULLET_RE);
    if (!m) return { kind: 'not-a-list' }; // 6. FOREIGN CONTENT (prose, table row, tab line, …)

    const indent = m[1].length;
    const marker = m[2];
    const info = parseNestedLabel(m[3]);

    // Bare-path guard applies to indent-0 bullets AND children.
    if (isBarePathBullet(marker, info)) return { kind: 'not-a-list' };

    if (indent === 0) {
      if (!isPlainLabel(info.label)) return { kind: 'not-a-list' };
      sawTop = true;
      currentContainer = i;
      currentContainerLabel = info.label;
      ownerOf[i] = -1;
      if (firstTopMarker === null) firstTopMarker = marker;
      lastBulletIndex = i;
      if (info.label === wanted) containers.push({ index: i, label: info.label, marker });
    } else {
      if (currentContainer === null) return { kind: 'not-a-list' }; // orphan child
      if (childIndentSeen === null) {
        childIndentSeen = indent;
        // C-cap: a >= 6-space "bullet" is a CommonMark indented-code line, not a child; the cap
        // stays below the code-block threshold and matches GitBook's 2/4-space convention.
        if (childIndentSeen < 2 || childIndentSeen > 4) return { kind: 'not-a-list' };
      } else if (indent !== childIndentSeen) {
        return { kind: 'not-a-list' }; // a second, distinct child indent
      }
      lastBulletIndex = i;
      ownerOf[i] = currentContainer;
      ownerLabelOf[i] = currentContainerLabel;
    }
  }

  if (!sawTop) return { kind: 'not-a-list' };

  // §5.4 resolution: a file with no child bullet anywhere defaults C = 2 (GitBook-standard,
  // within the 2..4 cap); every accepted bullet has a single-space marker, so a container's
  // content column is indent+2 and a child at C in [2,4] is always a valid sublist.
  const childIndent = childIndentSeen === null ? 2 : childIndentSeen;

  return { kind: 'ok', containers, childIndent, firstTopMarker, lastBulletIndex, ownerOf, ownerLabelOf };
}

// The single normalization every caller must apply to a manifest group_title before comparing it
// against a container's own label — deriving it here means the same group_title can never be
// compared under two different spellings, however many callers there are (round-3 review: an
// earlier wording named exactly two callers and was already under-inclusive by the time it was
// reviewed). Review flagged an un-shared, independently duplicated `.trim()` at each comparison
// site as exactly that seam: it is one `.trim()` today, but if the normalization ever gained a step
// (NFC, inner-whitespace collapse) an un-shared copy would keep the old spelling and start emitting
// false verdicts, with nothing going red.
//
// What the key is compared AGAINST is call-path-specific, not a property of this function —
// whatever comparison paths exist, each still derives its `group_title`-side key from here. Two
// paths exist today, illustrating the point rather than exhausting it: `containerOwnerScan`'s
// `ownerLabelOf` (the nested-list writer/verifier path), and `collectContainerHeadings`' headings-form
// container titles (the `findContainer` path, `:858`). The headings side is trimmed.
//
// `ownerLabelOf` is trimmed on the RAW and WIKILINK branches but NOT on `mdlink`, and the difference
// is easy to misread: `parseNestedLabel` trims the bullet's CONTENT at `:1143`, then the mdlink branch
// returns the link TEXT slice verbatim (`:1189`), so `- [ Admin ](admin.md)` yields ' Admin '.
// Measured, all three container spellings against `group_title: 'Admin'`:
//     `- Admin  `                -> ok          (raw, trimmed)
//     `- [[admin| Admin ]]`      -> ok          (wikilink, trimmed)
//     `- [ Admin ](admin.md)`    -> misplaced, foundContainer ' Admin '   (mdlink, NOT trimmed)
// That asymmetry is deliberate and load-bearing (round-18 HIGH): the writer reads the same label, so
// trimming it here would accept a container the writer itself treats as absent. It is pinned by the
// rule-5 UNTRIMMED test in chapter-paths.test.mjs — check that test before "simplifying" this.
function containerLabelKey(groupTitle) {
  return String(groupTitle).trim();
}

/**
 * Nested-list grouped index wiring, ABSENT-line path only. Pure: returns the fully-mutated index
 * line array; the runtime persists it. Reached only when findContainer(...) === {kind:'non-heading'}
 * AND step-0 found no existing line. NEVER mutates the input array; NEVER moves/deletes an existing
 * line (insert-only).
 *
 * Step 0 is the caller's IDEMPOTENCY guarantee, and it is not sufficient when a row's link text
 * defeats its target parse. When the child row this function emits uses `-`, an insert-only
 * transform that trusted step 0 would append that same target-breaking link on every publish; the
 * literal `present` check below bounds that case. With a `*`/`+` child carrying a grouped target,
 * the re-read postcondition refuses the raw-fallback row before it is written instead. The marker
 * is the new row's (`childMarkerUsed`), not a property of the file or necessarily of its container.
 * The literal check deliberately does NOT reuse step 0's target parse, since sharing that parse
 * would reproduce the `-` child blind spot; it can also return `present` for an ordinary exact link
 * when this function is called directly.
 *
 * @param {string[]} indexLines  index file split on '\n' (a CRLF file leaves a trailing '\r' per elem)
 * @param {string}   groupTitle  entry's current group_title (trimmed for comparison)
 * @param {string}   chapterLink the fully-formatted, MODE-CORRECT link the adapter already uses for
 *                               this profile ('[Items](admin/items.md)' path mode; '[[admin/items|Items]]'
 *                               wikilink mode). OPAQUE: this fn owns list STRUCTURE, caller owns link FORMAT.
 * @returns {{kind:'inserted', created:boolean, newLines:string[]}
 *         | {kind:'present', index:number}
 *         | {kind:'unwritable', field:'title'|'group_title'|'unknown'}
 *         | {kind:'multiple', matches:Array<{index:number, label:string}>}
 *         | {kind:'not-a-list'}}
 */
export function wireNestedListChapter(indexLines, groupTitle, chapterLink) {
  // §5.1 step 4: validateGroups permits a multiline group_title/link; embedding a '\r'/'\n' would
  // inject a foreign physical line the validator itself would reject — refuse rather than corrupt.
  if (/[\r\n]/.test(groupTitle) || /[\r\n]/.test(chapterLink)) return { kind: 'not-a-list' };

  // §5.1 steps 1-3 and 5-6, shared with the #330 verifier via the same private call.
  const prep = prepareIndexLines(indexLines);
  if (prep.kind === 'not-a-list') return { kind: 'not-a-list' };
  const { logical, eol: EOL, hadTerminalNewline, body: BODY } = prep;

  // Immediate guards on BODY.
  // YAML: MkDocs `nav:` / `- key: value` mapping structure (frontmatter already blanked).
  if (hasYamlMappingStructure(BODY)) return { kind: 'not-a-list' };
  const wanted = containerLabelKey(groupTitle);
  // Plain-label allowlist on the group_title (both sides — §5.1): a construct-bearing title could
  // emit a container that render-collides with an existing plain one, or fail to match one.
  if (!isPlainLabel(wanted)) return { kind: 'not-a-list' };

  // §5.1 single forward pass over BODY, shared with the #330 verifier via the same private scan.
  const scan = containerOwnerScan(BODY, wanted);
  if (scan.kind === 'not-a-list') return { kind: 'not-a-list' };
  const { containers, childIndent, firstTopMarker, lastBulletIndex } = scan;

  // §5.4 EOL-faithful emission, gated by a RE-READ POSTCONDITION [1.11.0].
  //
  // The writer used to emit whatever the caller's chapterLink and group_title produced, with a
  // plain-label check on the container label and none at all on the child row. A manifest value that
  // is legal everywhere upstream — a backtick run, an HTML comment, a U+2028, a `Token:` prefix,
  // a run of hyphens, a `/` on a line emitted with `*`/`+` — therefore got written into a file that was clean a
  // moment earlier, and THIS SAME SCANNER refused that file on every later run: nested-list
  // automation died for every chapter and every group in it, permanently, and the operator saw only
  // the generic manual halt naming no row.
  //
  // So the postcondition is not a list of forbidden characters — enumerating the scanner's rules
  // here would be a second copy that drifts, and a copy of a rule cannot notice a rule it never
  // copied. It RUNS the real gates over the real bytes about to be persisted: if our own reader
  // would reject what our own writer is about to hand back, we hand back nothing.
  const rereadRejects = (candidateLines) => {
    const re = prepareIndexLines(candidateLines);
    if (re.kind === 'not-a-list') return true;
    if (hasYamlMappingStructure(re.body)) return true;
    return containerOwnerScan(re.body, wanted).kind === 'not-a-list';
  };

  // Attribution, computed only on the failure path: swap ONE emitted line for a known-recognizable
  // stand-in and re-read. If that clears the rejection, that line is the culprit — which tells the
  // caller which MANIFEST FIELD to name in its halt. Derived by substitution rather than by parsing
  // the value, so it stays correct for causes nobody has found yet.
  const blame = (outLogical, emitted) => {
    for (const { index, standIn, field } of emitted) {
      const probe = outLogical.slice();
      probe[index] = standIn;
      const probeOut = probe.join(EOL) + (hadTerminalNewline ? EOL : '');
      if (!rereadRejects(probeOut.split('\n'))) return field;
    }
    return 'unknown';
  };

  const emit = (outLogical, created, emitted) => {
    const out = outLogical.join(EOL) + (hadTerminalNewline ? EOL : '');
    const newLines = out.split('\n');
    if (rereadRejects(newLines)) return { kind: 'unwritable', field: blame(outLogical, emitted) };
    return { kind: 'inserted', created, newLines };
  };

  if (containers.length >= 2) {
    return { kind: 'multiple', matches: containers.map((c) => ({ index: c.index, label: c.label })) };
  }

  if (containers.length === 1) {
    // SINGLE — insert after the last C-indent child in the container's child region (trailing
    // blanks stay after); if the region has no child bullet, immediately after the container.
    const k = containers[0].index;
    const containerMarker = containers[0].marker;
    let insertAt = k + 1;
    let childMarker = null; // marker of the LAST existing C-indent child seen in the region, if any
    for (let i = k + 1; i < logical.length; ) {
      const line = logical[i];
      if (line.trim() === '') {
        i += 1;
        continue;
      }
      const bm = line.match(NESTED_BULLET_RE);
      if (bm && bm[1].length === childIndent) {
        // Membership on the bullet's CONTENT, compared verbatim against the link the caller is
        // asking us to write. Content rather than the whole line, so a re-indented OR re-markered row
        // still counts as present — measured: a `-` file whose child row an operator re-markered to
        // `*` or `+` still answers `present`.
        //
        // Independently, and easy to conflate with the above: a `*`/`+` bullet whose content is a
        // BARE PATH is refused by isBarePathBullet (`:1257`, which requires `info.kind === 'raw'`)
        // before this walk runs, so such a file answers `not-a-list`. That is a marker x raw-content
        // rule, not a re-markering rule — a re-markered row carrying a normal link never reaches it.
        //
        // This guard is therefore MARKER-SCOPED, and the markers differ in the OUTCOME, not in the
        // diagnostics. Measured with a title carrying an unescaped `]` (the case this guard exists
        // for), emitted as `[Items]Beta](admin/items-beta.md)`: when the new child marker is `-`,
        // run 1 inserts, run 2 answers `present`, and exactly one row exists. When it is `*`/`+`, the emitted row's raw
        // text carries a path separator, so the re-read postcondition above (`rereadRejects`)
        // refuses the bytes and EVERY run answers `unwritable`/`title` — nothing is written, and an
        // unrelated chapter in an unrelated group on that same untouched file still answers
        // `inserted`. This guard decides nothing there: it finds no match, and the refusal happens
        // afterwards, at emit.
        //
        // The whole-file lockout is HISTORICAL rather than gone: on an index a 1.10.0 publish
        // already wrote such a `*`/`+` row into, isBarePathBullet fires on the row now ON DISK, so
        // containerOwnerScan answers `not-a-list` before this walk ever runs — for every chapter and
        // every group in the file, permanently (measured; the corresponding `-` row is unaffected). The
        // postcondition keeps NEW files out of that state; it cannot repair one already in it. No
        // wording here should imply this guard bounds either case.
        //
        // `present` is a REQUIREMENT on a PUBLISH-PATH caller, not a description of one: halt and
        // tell the operator, never retry. The in-module probe caller (verifyNonHeadingPlacement) is
        // deliberately exempt — see the `present` contract in chapter-paths.d.mts for both halves.
        if (bm[3] === chapterLink) return { kind: 'present', index: i };
        insertAt = i + 1;
        childMarker = bm[2];
        i += 1;
        continue;
      }
      break; // first line that is neither blank nor a C-indent child ends the region
    }
    // Reuse the existing children's marker so the inserted line stays in the SAME list block
    // (CommonMark starts a new list on a marker change); a container with no existing child has
    // no sibling marker to match, so fall back to the container's own marker (first-ever child).
    const childMarkerUsed = childMarker ?? containerMarker;
    const childLine = ' '.repeat(childIndent) + childMarkerUsed + ' ' + chapterLink;
    return emit(logical.slice(0, insertAt).concat([childLine], logical.slice(insertAt)), false, [
      // Only the child row is new here, so it is the only line that can be blamed. The stand-in is a
      // minimal row this scanner is known to accept, so a clean re-read means the real line's own
      // content — i.e. the chapter's manifest title — is what the reader rejected.
      { index: insertAt, standIn: ' '.repeat(childIndent) + childMarkerUsed + ' [x](x.md)', field: 'title' },
    ]);
  }

  // ZERO — create a bare-label container + child spliced immediately after the last bullet. The
  // container label is the TRIMMED group_title (validateGroups does not strip padding, which would
  // otherwise push the content column and misplace the child, R4-4); the container mirrors the bare
  // `## <group_title>` heading create (convergence-neutral — step 0 checks the CHAPTER line).
  const containerLine = firstTopMarker + ' ' + wanted;
  const childLine = ' '.repeat(childIndent) + firstTopMarker + ' ' + chapterLink;
  return emit(
    logical.slice(0, lastBulletIndex + 1).concat([containerLine, childLine], logical.slice(lastBulletIndex + 1)),
    true,
    // TWO new lines here, so blame must distinguish them. Container first: a container line that the
    // reader refuses (a bare-path shape on a `*`/`+` marker, a `Token:` prefix, a run of hyphens)
    // makes the child's own indent meaningless, so attributing to `group_title` is both the earlier
    // cause and the one whose repair fixes the other.
    [
      { index: lastBulletIndex + 1, standIn: firstTopMarker + ' x', field: 'group_title' },
      { index: lastBulletIndex + 2, standIn: ' '.repeat(childIndent) + firstTopMarker + ' [x](x.md)', field: 'title' },
    ],
  );
}

// ---------------------------------------------------------------------------------------------
// D6 — manual-migration boundary
// ---------------------------------------------------------------------------------------------

function classifyEntryDelta(oldEntry, newEntry) {
  if (newEntry === null || newEntry === undefined) {
    // old-only. Only a GROUPED removal is a migration matter (R9-F2) — a flat old-only entry is
    // ordinary deletion, not a boundary trigger.
    if (oldEntry.group === undefined) return null;
    return 'removal';
  }
  if (oldEntry === null || oldEntry === undefined) {
    // new-only. NEVER a migration matter, regardless of anyGroupFlip (R9-F2).
    return null;
  }

  const groupChanged = oldEntry.group !== newEntry.group;
  // F5: compared TRIMMED — a padding-only difference ('Admin' vs '  Admin  ') is not a real
  // title change (trimmedTitle is the single normalization every touchpoint reads through).
  const titleChanged = trimmedTitle(oldEntry) !== trimmedTitle(newEntry);
  const destinationGrouped = newEntry.group !== undefined;
  const sourceGrouped = oldEntry.group !== undefined;

  // The combined kind requires BOTH sides grouped (a genuine old-title -> new-title transition).
  // A flat->grouped add trivially has titleChanged=true (undefined -> required title) with no
  // meaningful "old title", so it stays a plain group-change (no title fact); grouped->flat is
  // already excluded by destinationGrouped (R12-F2).
  if (groupChanged && titleChanged && destinationGrouped && sourceGrouped) return 'group-and-title-change';
  if (groupChanged) return 'group-change';
  // A pure title change is only a migration matter when it fires on a still-grouped entry
  // (R12-F2: grouped->flat has no current title at all — the group-change branch above already
  // covers it and never carries a title fact).
  if (titleChanged && destinationGrouped) return 'title-change';
  return null;
}

/**
 * groupChanges(oldEntries, newEntries) — the D6 boundary trigger. Classifies every entry across
 * the retained/new-only/old-only domains and emits the per-entry change kind (never for a
 * new-only entry, per R9-F2), plus an informational `anyGroupFlip` (never itself a halt trigger —
 * see the write-time-canon principle, D6).
 *
 * @param {Array<{slug: string, group?: string, group_title?: string}>} oldEntries
 * @param {Array<{slug: string, group?: string, group_title?: string}>} newEntries
 * @returns {{changes: Array<{kind: string, slug: string, oldEntry: object|null, newEntry: object|null}>, anyGroupFlip: boolean}}
 */
export function groupChanges(oldEntries, newEntries) {
  const oldBySlug = new Map(oldEntries.map((e) => [e.slug, e]));
  const newBySlug = new Map(newEntries.map((e) => [e.slug, e]));
  const changes = [];

  for (const [slug, oldEntry] of oldBySlug) {
    const newEntry = newBySlug.get(slug) ?? null;
    const kind = classifyEntryDelta(oldEntry, newEntry);
    if (kind !== null) changes.push({ kind, slug, oldEntry, newEntry });
  }
  // new-only entries are intentionally never visited — they can never produce a kind (R9-F2).

  return { changes, anyGroupFlip: anyGroup(oldEntries) !== anyGroup(newEntries) };
}

/**
 * currentIndexExpectedTarget(profileLike, entry, vaultRelChaptersDir) — #295's export target: the
 * D6 index-target formula, direct-unit-testable in isolation (previously private, reached only
 * via manualMigrationChecklist). PURE helper — no fs, no realpath (that is #295's whole point) —
 * so it cannot itself discover or canonicalize the vault root. In wikilinks mode the fs-aware
 * CALLER (the obsidian-vault adapter) precomputes the canonical, vault-root-relative
 * `vaultRelChaptersDir` prefix — `relative(realpath(<vault root>), realpath(publish.chapters_dir))`
 * — and passes it in; this function only joins it onto the chapter's relative path (§1a). A raw,
 * uncanonicalized lexical ancestor of `publish.chapters_dir` is NOT equivalent under a
 * symlink-to-vault-subdirectory topology — canonicalizing both operands is the adapter's job,
 * never this pure module's (see obsidian-vault.md's worked symlink example).
 *
 * - wikilinks mode (Option A, #294): `posixJoin(vaultRelChaptersDir, chapterRelPath(entry))` with
 *   ONE terminal `.md` dropped — e.g. `vaultRelChaptersDir` `'handbook'`, entry `{slug:'orders'}`
 *   -> `'handbook/orders'`; grouped entry `{group:'admin', slug:'orders'}` -> `'handbook/admin/orders'`.
 *   The group axis rides on the prefix, so grouping DOES change the target (unlike the pre-1.8.0
 *   bare slug). The empty string `''` is a VALID prefix — the root topology (`chapters_dir` IS the
 *   vault root): `posixJoin('', 'items.md')` -> `'items.md'` -> `'items'`, the true single-segment
 *   vault-root path (§0a: resolves via Obsidian's robust tier-3 exact match, not the fragile tier).
 * - Fail loud (a caller bug, never a silent bare-slug fallback — that silent fallback was the
 *   #294 defect): throws when `vaultRelChaptersDir` is `null`/`undefined`, when it is absolute
 *   (`isAbsolute`), or when its first segment is `'..'` (escapes the vault root).
 * - path-link mode (`wikilinks: false`) is UNCHANGED: `relative(dirname(index_file),
 *   chapterFullPath)`, `.md` kept. `vaultRelChaptersDir` is ignored in path mode — engine-neutral
 *   (static-md hard-requires `wikilinks: false` and never has a vault root to compute).
 *
 * @param {{publish: {wikilinks: boolean, index_file: string, chapters_dir: string}}} profileLike
 * @param {{slug: string, group?: string}} entry
 * @param {string} [vaultRelChaptersDir]  wikilinks mode only — the precomputed, realpath'd,
 *   vault-root-relative delta to publish.chapters_dir (adapter-canonicalized; `''` means
 *   chapters_dir IS the vault root)
 * @returns {string}
 */
export function currentIndexExpectedTarget(profileLike, entry, vaultRelChaptersDir) {
  if (profileLike.publish.wikilinks) {
    if (vaultRelChaptersDir == null) {
      throw new Error(
        'currentIndexExpectedTarget: vaultRelChaptersDir is required in wikilinks mode — a ' +
          'silent bare-slug fallback resolves ambiguously across the whole vault (#294).',
      );
    }
    if (isAbsolute(vaultRelChaptersDir)) {
      throw new Error(
        `currentIndexExpectedTarget: vaultRelChaptersDir must be vault-root-relative, got absolute '${vaultRelChaptersDir}'.`,
      );
    }
    if (pathSegments(vaultRelChaptersDir)[0] === '..') {
      throw new Error(
        `currentIndexExpectedTarget: vaultRelChaptersDir '${vaultRelChaptersDir}' escapes the vault root ('..').`,
      );
    }
    return posixJoin(vaultRelChaptersDir, chapterRelPath(entry)).replace(/\.md$/, '');
  }
  return posixRelative(posixDirname(profileLike.publish.index_file), chapterFullPath(profileLike, entry));
}

// [1.12.0] Independent re-derivation of capture-record.mjs's chapterRecordPath — NOT an import.
// chapter-paths.mjs stays pure and dependency-free (no fs, no cross-module coupling), the same
// reason it re-implements its own path algebra instead of depending on node:path; capture-record.mjs
// states the identical rationale for not importing this module's private helpers. Verified
// byte-for-byte against the real capture-record.mjs source (`provenanceRoot`/`chapterRecordPath`,
// which compose `<publish.chapters_dir>/.provenance/chapters/<group>/<slug>.json`, grouped, or
// `.../chapters/<slug>.json` flat) rather than assumed from prose — this formula is ONLY for
// rendering a halt message naming where the operator should find/move the record, never for an
// actual read or write, so it does not carry the "one shared derivation" requirement chapterAssetDir
// does for W2/W5/W6 (capture-record.mjs's own docstring on chapterRecordPath). Presence is checked
// via `!== undefined` — this module's own established convention (chapterRelPath, outputDirTail),
// not capture-record.mjs's `entry?.group` truthy check, which would (incorrectly) treat a
// falsy-but-present group as flat; see the finding filed against capture-record.mjs for that gap.
function migrationRecordPath(profileLike, entry) {
  const fileName = `${entry.slug}.json`;
  const tail = entry.group !== undefined ? `${entry.group}/${fileName}` : fileName;
  return posixJoin(profileLike.publish.chapters_dir, '.provenance', 'chapters', tail);
}

/**
 * manualMigrationChecklist(profileLike, oldEntry|null, newEntry|null, vaultRelChaptersDir,
 * provenanceActive) — the per-delta-kind terminal-state FACT DESCRIPTORS the D6 convergence check
 * verifies. This function is pure and has no filesystem/index access, so it does not itself
 * evaluate met/unmet — it derives the EXPECTED VALUES (current derived paths, old derived paths,
 * index targets, capture-spec dir spellings) a caller checks the real world against. An entry
 * untouched by the delta (no kind under classifyEntryDelta) returns [].
 *
 * [1.12.0] `provenanceActive` gates the twelfth fact kind, `provenance-record` — the caller's OWN
 * re-assertion of this run's W1 ownership outcome, never inferred from `<root>/` existing on disk
 * (a profile that ran active once and later acquired an overlapping `capture.output_dir` still has
 * a populated `.provenance/` from a PRIOR run — see the plan's "guard is the W1 outcome and
 * explicitly NOT '<root>/' exists" rationale). REQUIRED whenever `kind` is not null — no default:
 * a caller passing anything other than a real `true`/`false` gets a thrown error, never a silently
 * incomplete checklist (see the guard below). This module has no in-repo caller outside its own
 * tests, so there was no pre-1.12.0 real caller to preserve byte-for-byte by quietly defaulting —
 * the parameter is new in 1.12.0 either way — and a silent `false` default is exactly the defect
 * class this release's W5 blocker already closed once (an opt-in-only capability that nothing real
 * ever opts into: every test constructs `true` by hand, and the one thing that would need to pass
 * it for real never does). Passing `false` explicitly still reproduces the pre-1.12.0 checklist
 * byte-for-byte — the difference is that "explicit" is now mandatory, not assumed. The fact itself
 * is present only for 'removal' and the two grouped-change kinds — a title-only change never moves
 * the record's path, so it carries no such fact regardless of `provenanceActive`'s value (the
 * boolean is still required there too, for the one uniform contract every real delta kind shares).
 *
 * @param {{capture: {output_dir: string}, publish: {chapters_dir: string, index_file: string, wikilinks: boolean}}} profileLike
 * @param {object|null} oldEntry
 * @param {object|null} newEntry
 * @param {string} [vaultRelChaptersDir]  wikilinks mode only — threaded into every
 *   currentIndexExpectedTarget call this function makes (see its own JSDoc, §1a)
 * @param {boolean} provenanceActive  this run's real W1 ownership outcome — REQUIRED whenever
 *   `kind` is not null (no default); throws when not strictly boolean
 * @returns {Array<object>} fact descriptors, each carrying a `kind` tag
 */
export function manualMigrationChecklist(profileLike, oldEntry, newEntry, vaultRelChaptersDir, provenanceActive) {
  const kind = classifyEntryDelta(oldEntry, newEntry);
  if (kind === null) return [];

  // Fail-loud guard (this module's established idiom — see currentIndexExpectedTarget's own
  // guards above): a silent default here is the exact defect shape the W5 blocker already cost
  // this release once — an opt-in-only capability (the twelfth fact kind, provenance-record) that
  // every test constructs by hand and no real caller has ever been written to pass. Requiring an
  // explicit boolean means a future real caller that forgets to thread this run's W1 ownership
  // outcome through gets a thrown error immediately, not a checklist and halt text that silently
  // omit the provenance-record move for an ACTIVE run.
  if (typeof provenanceActive !== 'boolean') {
    throw new Error(
      "manualMigrationChecklist: provenanceActive must be an explicit boolean — this run's real " +
        "W1 ownership outcome (capture-record.mjs's assertProvenanceOwnership/openCaptureRun " +
        'result: active iff ownership.ok and not ownership.skip), never omitted and never ' +
        'defaulted. A caller with no ownership signal yet must still decide and pass false ' +
        'explicitly — a silent default here would let an ACTIVE migration render its halt text ' +
        'and terminal-state facts with the provenance-record fact missing, exactly as if the run ' +
        'had never owned anything.',
    );
  }

  if (kind === 'removal') {
    const oldChapterPath = chapterFullPath(profileLike, oldEntry);
    const oldAssetDir = chapterAssetDir(profileLike, oldEntry);
    return [
      { kind: 'old-chapter-path-gone', path: oldChapterPath },
      { kind: 'old-asset-dir-gone', path: oldAssetDir },
      ...(provenanceActive
        ? [{ kind: 'provenance-record', oldPath: migrationRecordPath(profileLike, oldEntry), newPath: null }]
        : []),
      {
        kind: 'old-index-target-gone',
        form: profileLike.publish.wikilinks ? 'wikilink' : 'path',
        slug: oldEntry.slug,
        expectedTarget: currentIndexExpectedTarget(profileLike, oldEntry, vaultRelChaptersDir),
        oldContainerTitle: trimmedTitle(oldEntry) ?? null,
        // §1b: a pre-1.8.0 handbook may still carry the legacy BARE `[[slug]]` row for this
        // chapter (wikilinks mode only) — the caller's container-scoped legacy-bare-gone check
        // (§1b BLOCKER-2a) reads this alongside expectedTarget.
        legacyBareTarget: profileLike.publish.wikilinks ? oldEntry.slug : undefined,
      },
      {
        kind: 'no-live-capture-sink',
        oldDirQualified: oldAssetDir,
        oldDirTail: outputDirTail(oldEntry),
      },
      {
        kind: 'no-forbidden-wikilink',
        slug: oldEntry.slug,
        oldChapterRelPath: oldChapterPath,
      },
    ];
  }

  if (kind === 'title-change') {
    return [
      {
        kind: 'title-container',
        containerTitle: trimmedTitle(newEntry),
        oldContainerTitle: trimmedTitle(oldEntry),
      },
    ];
  }

  // 'group-change' or 'group-and-title-change'.
  const facts = [];
  const newChapterPath = chapterFullPath(profileLike, newEntry);
  const newAssetDir = chapterAssetDir(profileLike, newEntry);
  const oldChapterPath = chapterFullPath(profileLike, oldEntry);
  const oldAssetDir = chapterAssetDir(profileLike, oldEntry);
  const destinationGrouped = newEntry.group !== undefined;

  facts.push({ kind: 'current-chapter-path', path: newChapterPath });
  facts.push({ kind: 'current-asset-dir', path: newAssetDir });
  facts.push(
    destinationGrouped
      ? {
          kind: 'current-index-membership',
          expectedTarget: currentIndexExpectedTarget(profileLike, newEntry, vaultRelChaptersDir),
          grouped: true,
          containerTitle: trimmedTitle(newEntry),
        }
      : {
          kind: 'flat-membership',
          expectedTarget: currentIndexExpectedTarget(profileLike, newEntry, vaultRelChaptersDir),
        },
  );
  facts.push({
    kind: 'capture-spec-check',
    oldDirQualified: oldAssetDir,
    oldDirTail: outputDirTail(oldEntry),
  });
  facts.push({ kind: 'old-chapter-path-gone', path: oldChapterPath });
  facts.push({ kind: 'old-asset-dir-gone', path: oldAssetDir });
  if (provenanceActive) {
    facts.push({
      kind: 'provenance-record',
      oldPath: migrationRecordPath(profileLike, oldEntry),
      newPath: migrationRecordPath(profileLike, newEntry),
    });
  }

  const sourceWasGrouped = oldEntry.group !== undefined;
  // Under Option A (#294, vault-root-relative wikilinks) a group-slug rename ALWAYS changes the
  // vault-rel target string (`handbook/admin/items` -> `handbook/management/items`), so old and
  // new lines are never textually identical — the pre-1.8.0 "exactly one match under the shared
  // container" exception (R14-F3, which existed only because a title-preserving bare-`[[slug]]`
  // rename left old and new as the SAME string) has no live case under this formula and is
  // removed. The old target is now always expected GONE, in both modes. A pre-1.8.0 handbook may
  // still carry the legacy BARE `[[oldslug]]` row (wikilinks mode only) — that is a separate,
  // container-scoped concern the caller checks via `legacyBareTarget` (§1b BLOCKER-2a), not this
  // fact's `expectedTarget`.
  facts.push({
    kind: 'old-index-target-gone',
    form: profileLike.publish.wikilinks ? 'wikilink' : 'path',
    slug: oldEntry.slug,
    expectedTarget: currentIndexExpectedTarget(profileLike, oldEntry, vaultRelChaptersDir),
    oldContainerTitle: sourceWasGrouped ? trimmedTitle(oldEntry) : null,
    legacyBareTarget: profileLike.publish.wikilinks ? oldEntry.slug : undefined,
  });

  if (kind === 'group-and-title-change') {
    facts.push({
      kind: 'title-container',
      containerTitle: trimmedTitle(newEntry),
      oldContainerTitle: trimmedTitle(oldEntry),
    });
  }

  return facts;
}

function findFact(facts, kind) {
  return facts.find((f) => f.kind === kind);
}

function renderChangeLine(change, facts) {
  const { kind, slug, oldEntry, newEntry } = change;

  if (kind === 'removal') {
    const oldChapterPath = findFact(facts, 'old-chapter-path-gone').path;
    const oldAssetDir = findFact(facts, 'old-asset-dir-gone').path;
    const record = findFact(facts, 'provenance-record');
    // [1.12.0] The provenance-record fact is present ONLY when this run's W1 ownership outcome
    // was active (manualMigrationChecklist's own `provenanceActive` gate) — its absence, never a
    // null/placeholder path, is what "omit the whole fragment on a skipped run" means, so a
    // skip-path caller reproduces this line byte-for-byte unchanged from before 1.12.0.
    return record
      ? `  ${slug}: removed — delete ${oldChapterPath}, ${oldAssetDir}, its index line, and its record ${record.oldPath} (was under container '${trimmedTitle(oldEntry)}')`
      : `  ${slug}: removed — delete ${oldChapterPath}, ${oldAssetDir}, and its index line (was under container '${trimmedTitle(oldEntry)}')`;
  }

  if (kind === 'title-change') {
    return `  ${slug}: container title '${trimmedTitle(oldEntry)}' -> '${trimmedTitle(newEntry)}'`;
  }

  // 'group-change' or 'group-and-title-change'.
  const newChapterPath = findFact(facts, 'current-chapter-path').path;
  const newAssetDir = findFact(facts, 'current-asset-dir').path;
  const oldChapterPath = findFact(facts, 'old-chapter-path-gone').path;
  const oldAssetDir = findFact(facts, 'old-asset-dir-gone').path;
  const sourceWasGrouped = oldEntry.group !== undefined;
  const suffix = sourceWasGrouped ? `; was under container '${trimmedTitle(oldEntry)}'` : '';
  let line = `  ${slug}: ${oldChapterPath} -> ${newChapterPath}; assets ${oldAssetDir} -> ${newAssetDir}`;
  const record = findFact(facts, 'provenance-record');
  if (record) {
    line += `; record ${record.oldPath} -> ${record.newPath}`;
  }
  line += suffix;
  if (kind === 'group-and-title-change') {
    line += `; container title '${trimmedTitle(oldEntry)}' -> '${trimmedTitle(newEntry)}'`;
  }
  return line;
}

/**
 * The production halt-text formatter (D6 "Halt texts" — exact strings). `changes` is
 * `groupChanges(...).changes`; `checklists[i]` is `manualMigrationChecklist(profileLike,
 * changes[i].oldEntry, changes[i].newEntry, vaultRelChaptersDir, provenanceActive)` (parallel
 * arrays) — the checklist facts are where the rendered derived paths come from, since this
 * formatter itself takes no profileLike. `vaultRelChaptersDir` and `provenanceActive` must be the
 * SAME value for every entry in one run (this run's own wikilinks prefix and W1 ownership
 * outcome — never re-decided per entry); an earlier revision of this very example showed a stale
 * 3-argument call that predated both parameters, which is exactly the kind of drift this comment
 * must not repeat the next time `manualMigrationChecklist` grows another one. With
 * `scanFailures`, renders the scan-failure variant instead, which EMBEDS the full original
 * migration record verbatim (R13-F3) so a context-free re-run can reconstruct every terminal
 * check from the text alone (R10-F5, R27-F3, R28-F1).
 *
 * @param {Array<{kind: string, slug: string, oldEntry: object|null, newEntry: object|null}>} changes
 * @param {Array<Array<object>>} checklists
 * @param {Array<{chapter: string, line: number, target: string}>} [scanFailures]
 * @returns {string}
 */
export function renderManualMigrationHalt(changes, checklists, scanFailures) {
  const recordLines = changes.map((change, i) => renderChangeLine(change, checklists[i]));

  if (scanFailures && scanFailures.length > 0) {
    const detail = scanFailures.map((f) => `${f.chapter}:${f.line} -> ${f.target}`).join(', ');
    return [
      `Post-migration link scan failed (${scanFailures.length} broken): ${detail}.`,
      ...recordLines,
      'Fix the listed links, then re-run — the re-run MUST re-verify the terminal facts above, repeat the handbook-wide link scan, and re-run the touched-chapter gates, in that order, before this migration counts as complete.',
    ].join('\n');
  }

  return [
    'This manifest change requires manual group migration (not automated in 1.5.0):',
    ...recordLines,
    'Follow the manual migration recipe in references/revalidation.md, then re-run.',
  ].join('\n');
}

// ---------------------------------------------------------------------------------------------
// D6 — capture-spec red-flag predicate [R15-F1/F3][R16-F2][R17-F1]
// ---------------------------------------------------------------------------------------------

const STRING_DELIMITERS = new Set(["'", '"', '`']);

/**
 * specReferencesDir(specText, dir) — the two-sided boundary-aware RED-FLAG literal match. NOT a
 * sink classifier (D6/R14-F1 dropped that entirely): a hit is sound negative evidence (the spec
 * still writes to the removed dir, so the migration fact is UNMET); an absence proves nothing and
 * always falls through to explicit user confirmation.
 *
 * Trailing boundary: `dir` is followed by a path separator ('/'), a string-literal quote, or the
 * end of text — so a file INSIDE the dir counts as a reference (`admin/orders/capture.png`).
 * Leading boundary — the terminal invariant (rev 17/18, closing five rounds of character-class
 * holes): the occurrence starts at the very beginning of `specText`, OR is immediately preceded
 * by the opening delimiter of a string literal (`'`, `"`, backtick). On POSIX almost any
 * character can appear in a path component, so no "path-char" character class is ever a sound
 * leading boundary — `legacy-admin/orders`, `legacy+admin/orders`, and `éadmin/orders` are all
 * legitimate, DIFFERENT dirs from `admin/orders`, and a leading '/' is not a boundary either
 * (`screens/admin/orders` must not false-flag). This is a DELIBERATE, asymmetric miss: a false
 * positive here permanently deadlocks convergence (the checklist fact would never clear), while a
 * false negative is safe by design — the confirmation backstop covers it.
 *
 * @param {string} specText
 * @param {string} dir
 * @returns {boolean}
 */
export function specReferencesDir(specText, dir) {
  if (!dir) return false;
  const text = String(specText);
  const needle = String(dir);
  let from = 0;
  while (true) {
    const i = text.indexOf(needle, from);
    if (i === -1) return false;
    const before = i > 0 ? text[i - 1] : null;
    const afterIndex = i + needle.length;
    const after = afterIndex < text.length ? text[afterIndex] : null;

    const leadingOk = before === null || STRING_DELIMITERS.has(before);
    const trailingOk = after === null || after === '/' || STRING_DELIMITERS.has(after);
    if (leadingOk && trailingOk) return true;

    from = i + 1;
  }
}

// ---------------------------------------------------------------------------------------------
// D6 — forbidden-target wikilink predicate [R15-F2/F3][R16-F1][R17-F2][R18-F2]
// ---------------------------------------------------------------------------------------------

const CHAPTER_WIKILINK_RE = /\[\[([^\]]+)\]\]/g;

// R3-F3/R4-F1: an inert `[[orders]]` occurrence — inline code, a fenced code block, an HTML
// comment, or a backslash-escaped `\[[...]]` — is documentation ABOUT the syntax, never a
// rendered link, and must never make the removal fact UNMET; an UNMET-forever with no legitimate
// way to clear it (short of deleting a legitimate doc example) is worse than the miss a stripped-
// but-real link would be (which the separate handbook-wide resolution scan backstops, same
// asymmetric-miss reasoning as specReferencesDir/isComponentSuffixMatch above). Both stripInertContexts
// (fenced/inline-code/HTML-comment stripping) and isEscaped (backslash-run check) are the SHARED
// helpers defined above — see stripInertContexts's own comment for why a single left-to-right
// pass replaced the earlier chained-.replace() approach.

function parseWikilinkTarget(raw) {
  // The target ends at the first alias/heading/block delimiter (`|`, `#`, `^`).
  const target = raw.split(/[|#^]/, 1)[0];
  // Strip ONE terminal .md, ASCII case-insensitive (Obsidian target equivalence: [[note.md]] ==
  // [[note]]). Applies to both unqualified AND qualified targets (R18-F2) — an unqualified-only
  // fold would let a qualified stale reference like [[Admin/Orders.MD]] escape.
  return target.replace(/\.md$/i, '').trim();
}

// ASYMMETRIC component-aligned suffix test (the plan's letter): `target` is a suffix of `old`
// only when target.length <= old.length — old can never be "a suffix of" a shorter target. A
// LONGER, vault-root-anchored spelling of the removed path (e.g. [[vault/handbook/admin/orders]]
// when oldChapterRelPath is `handbook/admin/orders.md`) therefore does NOT match here; it points
// at a file that no longer exists, so the separate handbook-wide RESOLUTION scan catches it as a
// broken link — a backstopped miss, per the plan's deliberate err-toward-missing direction. A
// symmetric test (matching in either length direction) would instead PERMANENTLY DEADLOCK the
// removal fact for any foreign note whose own path happens to tail-contain the old path (e.g. a
// real, kept note at `x/handbook/admin/orders.md`): every qualified spelling of that note
// tail-aligns with the (now shorter) old path, so the "converges via a further-qualified
// spelling" escape hatch the plan relies on would never actually exist. False-forbid (an
// unbreakable deadlock) is strictly worse than a miss (the resolution scan has a backstop for
// it) — see chapterHasWikilinkTo below.
function isComponentSuffixMatch(target, old) {
  if (target.length === 0 || target.length > old.length) return false;
  const offset = old.length - target.length;
  return target.every((seg, i) => seg === old[offset + i]);
}

/**
 * chapterHasWikilinkTo(chapterText, slug, oldChapterRelPath) — the forbidden-target predicate for
 * the removal handbook-wide scan (D6). `oldChapterRelPath` is the chapters_dir-QUALIFIED old
 * chapter path (e.g. `handbook/admin/orders.md`) — qualifying it makes the component-suffix
 * comparison below vault-root-anchoring-agnostic.
 *
 * R3-F3: NON-RENDERED occurrences never count — fenced code blocks, inline code spans, HTML
 * comments, and backslash-escaped `\[[...]]` are stripped/skipped first (`stripInertContexts` /
 * `isEscaped`), so a leftover documentation example quoting the removed chapter's wikilink
 * syntax can never deadlock this fact UNMET forever.
 *
 * Parses every `[[...]]` target through the `|`/`#`/`^` delimiters, strips one terminal `.md`
 * case-insensitively, then classifies:
 *   (a) UNQUALIFIED (no '/'): forbidden iff the basename equals `slug` case-insensitively — these
 *       resolve by basename in Obsidian and can silently retarget a same-basename foreign note.
 *   (b) QUALIFIED (contains '/'): forbidden iff its components are a component-aligned,
 *       case-insensitive suffix of oldChapterRelPath's components (target length <= old length;
 *       ASYMMETRIC — see isComponentSuffixMatch's own comment for why the reverse direction is
 *       deliberately NOT matched) — an explicit link to the removed location. A DIFFERENTLY-
 *       qualified path (e.g. `archive/orders` when the old path was `handbook/admin/orders.md`)
 *       is PERMITTED: it is a deliberate correction to a user-owned foreign note, not a reference
 *       to the removed chapter. A LONGER, vault-rooted spelling of the OLD path itself is also
 *       permitted here by design — it is caught instead by the separate handbook-wide resolution
 *       scan (it points at a deleted file and fails to resolve), never by this fact.
 *
 * @param {string} chapterText
 * @param {string} slug
 * @param {string} oldChapterRelPath
 * @returns {boolean}
 */
export function chapterHasWikilinkTo(chapterText, slug, oldChapterRelPath) {
  const oldNoExt = String(oldChapterRelPath).replace(/\.md$/i, '');
  const oldComponents = pathSegments(oldNoExt).map((s) => s.toLowerCase());
  const wantedSlug = String(slug).toLowerCase();
  const sanitized = stripInertContexts(String(chapterText));

  for (const m of sanitized.matchAll(CHAPTER_WIKILINK_RE)) {
    if (isEscaped(sanitized, m.index)) continue;
    const target = parseWikilinkTarget(m[1]);
    if (!target) continue;

    if (!target.includes('/')) {
      if (target.toLowerCase() === wantedSlug) return true;
      continue;
    }

    const targetComponents = pathSegments(target).map((s) => s.toLowerCase());
    if (isComponentSuffixMatch(targetComponents, oldComponents)) return true;
  }
  return false;
}

// #330 — the fixed probe link the shape-recognition predicate below emits (and discards).
//
// [1.11.0] The round-9 justification for this constant said any newline-free chapterLink is
// accept/decline-equivalent, because the writer read chapterLink ONLY for embedded newlines and for
// emission. The membership guard added in 1.11.0 makes that false: chapterLink is now also compared
// against existing child bullets. So the probe's value IS observable — an index that already carries
// a row whose bullet content equals this exact string makes the writer answer `present` instead of
// `inserted`. Reproduced, not hypothesised. Rule 4 below therefore accepts BOTH outcomes explicitly:
// both mean "the writer recognized this shape and resolved exactly one container", which is the only
// question rule 4 asks, and the probe's emission is discarded either way.
const NON_HEADING_PLACEMENT_PROBE_LINK = '[probe](__verify-non-heading-placement-probe__.md)';

/**
 * #330 — present-line placement verification for the nested-list index form. Five-rule decision
 * table, first applicable rule wins (rules 1-2 decide on MATCH CARDINALITY alone, so rules 3-5 are
 * reached only for a file holding exactly one selected-target match):
 *   1. zero selected-target matches                          -> inconsistent (fail-closed)
 *   2. more than one selected-target match                   -> inconsistent
 *   3. the single match lies inside the leading-frontmatter span -> unverifiable
 *   4. the writer's own predicate declines the shape (not-a-list/multiple) -> unverifiable
 *   5. otherwise, compare the container the writer itself resolved -> ok / misplaced
 *
 * `selectedTarget` is the target the CALLER already selected (the Obsidian adapter's union scan
 * over the qualified/legacy-bare spellings picks one before placement checking) — using it, not a
 * bare expected target, lets a legitimately-present legacy row verify instead of a false
 * `inconsistent`.
 *
 * Shape recognition (rule 4) is DELEGATED to the writer's own `wireNestedListChapter` predicate
 * (fixed probe link, emission discarded) rather than re-implemented — a from-scratch YAML/nav
 * detector is measurably holed (e.g. `- Admin :` vs a real YAML parser's key), so the writer's own
 * accepted class is the only sound source of truth. The container walk (rule 5) runs over the
 * WRITER's own prepared BODY via the shared `containerOwnerScan`, never over `indexView` — the two
 * arrays have distinct, deliberate jobs: matches come from `indexView` because the verifier must
 * see exactly what the caller saw; the container walk runs over BODY because it must decide exactly
 * what the writer decided (a `not-a-list` match is impossible for the walk to see: BODY is
 * sanitization-stable and index-aligned with `indexView` on every file that reaches rule 5).
 *
 * @param {string[]} indexLines
 * @param {string} selectedTarget
 * @param {string} groupTitle
 * @param {{wikilink?: boolean}} [options]
 * @returns {{kind: 'ok'}
 *         | {kind: 'misplaced', foundContainer: string|null}
 *         | {kind: 'inconsistent'}
 *         | {kind: 'unverifiable'}}
 */
export function verifyNonHeadingPlacement(indexLines, selectedTarget, groupTitle, options = {}) {
  const { wikilink = false } = options;

  // Rules 1-2: cardinality alone, fail-closed. A contradiction (the caller reported present, the
  // verifier finds zero or several matches for the SAME selected target) is worth a manual halt
  // regardless of file shape. Match indices come straight from locateChapterLine's own loop —
  // never re-derive them with a second match loop over indexView (round-2 review: an earlier
  // revision did exactly that, re-implementing locateChapterLine's loop line-for-line, and was
  // caught as the second recognizer this design exists to prevent).
  const { matches } = locateChapterLine(indexLines, selectedTarget, { wikilink });
  if (matches.length !== 1) return { kind: 'inconsistent' };
  const matchIndex = matches[0].index;

  // Evaluation-order precondition, not a sixth rule: `prepareIndexLines` can refuse a file that
  // still holds exactly one match (e.g. a lone stray '\r') — reading `span`/`body` off a refusal
  // would destructure fields that do not exist. The writer would refuse the SAME file at rule 4
  // anyway, so this adds no row to the table.
  const prep = prepareIndexLines(indexLines);
  if (prep.kind === 'not-a-list') return { kind: 'unverifiable' };

  // Rule 3: a match inside a leading frontmatter block is never verified — the writer's own BODY
  // blanks frontmatter while `indexView` does not (the shipped 1.10.0 disagreement, filed as #337),
  // so a present match there cannot be soundly judged either way.
  if (prep.span !== null && matchIndex >= prep.span.start && matchIndex < prep.span.endExclusive) {
    return { kind: 'unverifiable' };
  }

  // Rule 4: shape recognition, delegated. `groupTitle` is the real value (its own newline guard
  // must fire identically to the writer's); only `chapterLink` is replaced with a fixed probe.
  const shapeVerdict = wireNestedListChapter(indexLines, groupTitle, NON_HEADING_PLACEMENT_PROBE_LINK);
  // Written as a positive accept-list, not as a negative decline-list. The negative form silently
  // acquired a third accepted outcome when 1.11.0 added `present`, and nothing went red; a future
  // outcome must fail this gate until someone decides it belongs here.
  if (shapeVerdict.kind !== 'inserted' && shapeVerdict.kind !== 'present') return { kind: 'unverifiable' };

  // Rule 5: the container comparison, via the writer's own shared scan. Structurally, `scan.kind`
  // cannot be 'not-a-list' here — an `inserted` or `present` shapeVerdict already proves the writer's
  // own internal containerOwnerScan(prep.body, wantedLabel) call succeeded on this exact body/label
  // (prepareIndexLines is pure, so the writer's internal call reproduces the same `prep.body`) —
  // but the branch stays, matching this file's own style of checking every `{kind, ...}` result
  // rather than trusting an invariant, and failing toward the safe direction (unverifiable costs
  // only verification; never a false `ok`) if it were ever wrong.
  const wantedLabel = containerLabelKey(groupTitle);
  const scan = containerOwnerScan(prep.body, wantedLabel);
  if (scan.kind === 'not-a-list') return { kind: 'unverifiable' };

  const owner = scan.ownerOf[matchIndex];
  if (owner === -1 || owner === undefined) return { kind: 'misplaced', foundContainer: null };
  // The writer's own parsed label, reused rather than re-derived, so the verifier can never disagree
  // with the writer about what a container is called. NEVER re-trim it here — see the trimming note
  // above containerLabelKey for which parse branches trim and why the mdlink one deliberately does not.
  const ownerLabel = scan.ownerLabelOf[matchIndex];
  if (ownerLabel === wantedLabel) return { kind: 'ok' };
  return { kind: 'misplaced', foundContainer: ownerLabel };
}

// ---------------------------------------------------------------------------------------------
// [1.12.0] Image-destination API — build-provenance completeness ("Extraction" / "The
// completeness rule (W5)" in the 1.12.0 plan). Still pure, still dependency-free: `filenames` is
// the caller's own directory listing (W5 already lists the asset dir for hashing), so this module
// never re-lists a directory itself — a second listing is a second chance to disagree with the
// first. Existing call-path logic above is untouched; everything below is new.
// ---------------------------------------------------------------------------------------------

// Splits on a literal '/' only — NEVER '\\' — unlike rawSegments above. A raw directory-listing
// filename may contain a literal backslash BYTE as part of its own name (not a separator); reusing
// rawSegments/posixJoin for the round-trip check below would silently re-run the file through the
// SAME backslash-aware algebra embedPath already used to build the candidate, so the comparison
// would agree with itself regardless of what the file actually was — a vacuous self-comparison.
function literalSlashSegments(p) {
  return String(p).split('/');
}

// True iff `candidate` (embedPath's actual output for directory entry `f`) resolves, by LITERAL
// '/'-only path arithmetic against the chapter's own directory, back to exactly the file that
// generated it. `f` may be a real multi-segment subdirectory entry ('sub/a.png') or a single
// filename that merely CONTAINS a backslash byte ('sub\stale.png') — the two are indistinguishable
// after they both pass through posixJoin/embedPath, because rawSegments treats '\\' as a separator
// too (so a stray Windows-authored profile path still normalizes). This is what tells them apart
// again: it resolves the CANDIDATE (embedPath's own output, always '/'-joined) against
// dirname(chapterFile) using ONLY '/' as a separator, and compares the result — segment by segment
// — against assetDir's segments followed by f's OWN literal '/'-only segments. For
// 'sub\stale.png' the two disagree (the candidate resolves to ['sub','stale.png'], two segments,
// while f's literal segments are the single ['sub\\stale.png']); for a genuine 'sub/a.png' entry
// they agree, because there was no backslash for the two algebras to disagree about.
function embedCandidateRoundTrips(chapterFile, assetDir, candidate, f) {
  const resolved = literalSlashSegments(posixDirname(chapterFile)).filter((s) => s !== '');
  for (const seg of literalSlashSegments(candidate)) {
    if (seg === '' || seg === '.') continue;
    if (seg === '..') {
      if (resolved.length > 0 && resolved[resolved.length - 1] !== '..') resolved.pop();
      else resolved.push('..');
      continue;
    }
    resolved.push(seg);
  }
  const expected = [
    ...literalSlashSegments(assetDir).filter((s) => s !== ''),
    ...literalSlashSegments(f).filter((s) => s !== ''),
  ];
  return resolved.length === expected.length && resolved.every((seg, i) => seg === expected[i]);
}

// The closed, renderer-invariant destination character subset (plan: "Why a subset and not a
// renderer model", measured against Pandoc 3.x and markdown-it 14.3.0): an optional run of one or
// more '../' climbs, then one or more segments drawn only from ASCII alphanumerics, '.', '_' and
// '-', joined by a single '/'. The moment a byte falls outside it, at least one of the two
// supported renderers rewrites the destination on its way to `src`, which is exactly the
// wrong-file-binding risk this gate exists to close — so anything outside it halts rather than
// being compared to a candidate at all.
const CANDIDATE_CHARSET_RE = /^(?:\.\.\/)*[A-Za-z0-9._-]+(?:\/[A-Za-z0-9._-]+)*$/;

// A dedicated Error subclass for a candidate-generation gate failure — thrown rather than
// returned, so buildEmbedCandidates' own success type stays the plain Map its signature promises,
// matching posixRelative's existing throw-on-config-error precedent above. expectedAssets is the
// one production caller that must convert this into its own {ok:false, halt} shape without parsing
// a message string; a caller invoking buildEmbedCandidates directly (a test, or a future consumer)
// sees an ordinary thrown Error either way.
class EmbedCandidateHalt extends Error {
  constructor(construct) {
    super(construct);
    this.name = 'EmbedCandidateHalt';
    this.construct = construct;
  }
}

// The legacy static-md spelling is offered only when BOTH conditions hold, checked BEFORE the
// candidate is generated (never after, which would turn a filter into a gate — see the plan's
// discussion of the degenerate layout): the active target is 'static_md' — the RAW profile
// spelling, never the '-'-hyphenated adapter filename — and the entry is genuinely flat AND
// non-degenerate. Nondegeneracy is compared over the CANONICAL relative prefix from
// dirname(chapterFile) to outputDir, never a raw string-equality: legacyStaticEmbedPath normalizes
// '/', '\\' and dot segments before constructing its result, so several raw spellings of the same
// degenerate layout (chapter-paths.mjs:229 pins the leading-slash quirk) must all be excluded
// identically.
function isLegacyCandidateEligible(profileLike, entry, chapterFile, target) {
  if (target !== 'static_md') return false;
  if (entry.group !== undefined) return false;
  return posixRelative(posixDirname(chapterFile), profileLike.capture.output_dir) !== '';
}

/**
 * buildEmbedCandidates(profileLike, entry, chapterFile, filenames, target) — the ONLY place a
 * candidate destination set is constructed; expectedAssets below calls this itself, so no caller
 * can hand in a hand-written candidate map. For every directory-listing entry `f` in `filenames`,
 * computes the exact destination string the adapter would emit (embedPath) — and, when the entry
 * is a genuinely flat, non-degenerate static_md chapter, also its retained legacy spelling
 * (legacyStaticEmbedPath), which maps to the SAME key `f` as the current spelling. Every candidate
 * is checked against two gates before being added — a round-trip back to the file that generated
 * it (embedCandidateRoundTrips), and the closed renderer-invariant character subset
 * (CANDIDATE_CHARSET_RE) — plus a defensive current-vs-legacy union-collision check; any of the
 * three throws an EmbedCandidateHalt naming the file rather than silently dropping or guessing.
 *
 * @param {ProfileLike} profileLike
 * @param {ChapterEntry} entry
 * @param {string} chapterFile
 * @param {string[]} filenames  the caller's own directory listing — never re-listed here
 * @param {string} target  the RAW profile value ('static_md' / 'obsidian_vault')
 * @returns {Map<string, string>} candidate destination -> the directory entry that generated it
 */
export function buildEmbedCandidates(profileLike, entry, chapterFile, filenames, target) {
  const assetDir = chapterAssetDir(profileLike, entry);
  const legacyEligible = isLegacyCandidateEligible(profileLike, entry, chapterFile, target);
  const candidates = new Map();

  const add = (candidate, f) => {
    if (!CANDIDATE_CHARSET_RE.test(candidate)) {
      throw new EmbedCandidateHalt(
        `embed candidate for '${f}' contains a character outside the renderer-invariant subset: '${candidate}'`,
      );
    }
    if (!embedCandidateRoundTrips(chapterFile, assetDir, candidate, f)) {
      throw new EmbedCandidateHalt(`embed candidate for '${f}' does not round-trip back to it: '${candidate}'`);
    }
    const existing = candidates.get(candidate);
    if (existing !== undefined && existing !== f) {
      throw new EmbedCandidateHalt(
        `embed candidate collision: '${candidate}' is generated by both '${existing}' and '${f}'`,
      );
    }
    candidates.set(candidate, f);
  };

  for (const f of filenames) {
    add(embedPath(chapterFile, assetDir, f), f);
    if (legacyEligible) {
      add(legacyStaticEmbedPath(chapterFile, profileLike.capture.output_dir, entry.slug, f), f);
    }
  }
  return candidates;
}

/**
 * isCanonicalAssetKey(key) — the structural-only predicate a stored record key must satisfy,
 * shared by both the run record's and the chapter record's readers. Rejects only what CANNOT be a
 * relative path inside the asset directory: a leading '/', an empty segment, and the segments
 * '.'/'..'. It constrains NO characters at all — a literal backslash and a segment literally named
 * '%2e%2e' are both legal POSIX names a real directory snapshot can produce, and a reader rejecting
 * them would reject a record its own writer just wrote.
 *
 * @param {unknown} key
 * @returns {boolean}
 */
export function isCanonicalAssetKey(key) {
  if (typeof key !== 'string' || key === '' || key.startsWith('/')) return false;
  return key.split('/').every((seg) => seg !== '' && seg !== '.' && seg !== '..');
}

// The escape-aware, DEPTH-COUNTING generalization of findLinkOpeners's label scan above
// (chapter-paths.mjs:518-523), needed because an image's alt text may legitimately contain a
// literal '[' ("![A [Beta]](img.png)") — a shape findLinkOpeners declines by design (nested-bracket
// labels are supported here, never silently dropped). Returns the index of the matching ']'
// (depth back to 0), or -1 when the label never closes.
function findNestedLabelEnd(text, labelStart) {
  return scanBalanced(text, labelStart, '[', ']', { stopAtNewline: false });
}

// The escape-aware depth counter both scans above are: from `start`, return the index of the
// `close` that brings depth back to 0, or -1 when it never arrives. A backslash escapes whatever
// follows it (the same escape-skip every other scan in this module uses), so an escaped delimiter
// never moves the depth. `stopAtNewline` is the only behavioural difference between the two
// callers: a link DESTINATION never legitimately spans a line break, so hitting one is treated the
// same as never closing, while an image LABEL may wrap freely.
function scanBalanced(text, start, open, close, { stopAtNewline }) {
  let depth = 0;
  let i = start;
  while (i < text.length && !(stopAtNewline && text[i] === '\n')) {
    if (text[i] === '\\') {
      i += 2;
      continue;
    }
    if (text[i] === open) {
      depth += 1;
      i += 1;
      continue;
    }
    if (text[i] === close) {
      if (depth === 0) return i;
      depth -= 1;
      i += 1;
      continue;
    }
    i += 1;
  }
  return -1;
}

// The same balanced-paren / angle-wrapped destination scan findMarkdownLinkGroups uses above
// (chapter-paths.mjs:562-597), factored so the image scanner below can start from an arbitrary
// absolute offset (just past the opening '(') instead of a per-line opener list. A destination
// never legitimately spans a line break, wrapped or not, so hitting one is treated the same as
// never finding a close. Returns the index of the closing ')', or -1.
function scanDestinationGroup(text, openParenIndex) {
  let i = openParenIndex + 1;
  if (text[i] === '<') {
    const gt = text.indexOf('>', i + 1);
    i = gt === -1 ? text.length : gt + 1;
  }
  return scanBalanced(text, i, '(', ')', { stopAtNewline: true });
}

// Scans `text` for every unescaped '![' — the raw-text accounting marker the whole
// completeness-or-halt contract is built on (plan: "every unescaped ![ in the RAW text must be
// accounted for"). Escape parity is checked on the '!' itself (isEscaped), matching every other
// escape-skip in this module. Classifies each marker by what follows its (nested-bracket-aware)
// label: 'inline' when a destination GROUP closes (the raw text between the parens/angles, not yet
// decoded — parseMdLinkDestination does that); 'reference' for any of the three reference-image
// shapes (full/collapsed/shortcut — none of which this release resolves, by design);
// 'unterminated' when the label or the destination group never closes. This function never
// consults inert context (fences/code spans/comments) at all — that is the caller's job, achieved
// by running this same scan over TWO different views of the text (see expectedAssets below).
function findImageMarkers(text) {
  const markers = [];
  let i = 0;
  while (i < text.length) {
    if (text[i] === '!' && text[i + 1] === '[' && !isEscaped(text, i)) {
      const offset = i;
      const labelEnd = findNestedLabelEnd(text, i + 2);
      if (labelEnd === -1) {
        markers.push({ offset, kind: 'unterminated' });
        i += 2;
        continue;
      }
      const after = labelEnd + 1;
      if (text[after] === '(') {
        const close = scanDestinationGroup(text, after);
        if (close === -1) {
          markers.push({ offset, kind: 'unterminated' });
          i = after + 1;
          continue;
        }
        markers.push({ offset, kind: 'inline', rawDestination: text.slice(after + 1, close) });
        i = close + 1;
        continue;
      }
      // '![alt][ref]' (full) or '![ref][]' (collapsed) both open with '[' right after the label;
      // '![ref]' (shortcut) is neither — all three are reference forms this release refuses.
      markers.push({ offset, kind: 'reference' });
      i = text[after] === '[' ? after + 1 : after;
      continue;
    }
    i += 1;
  }
  return markers;
}

function lineStartOf(text, offset) {
  let start = offset;
  while (start > 0 && text[start - 1] !== '\n') start -= 1;
  return start;
}

function lineNumberOf(text, offset) {
  let line = 1;
  for (let i = 0; i < offset && i < text.length; i += 1) {
    if (text[i] === '\n') line += 1;
  }
  return line;
}

// Column of `uptoOffset`, tab-expanded to 4-column stops from `lineStart` — the plan's own stated
// rule ("tabs are expanded to four-column stops before any of this is measured").
function tabExpandedColumn(text, lineStart, uptoOffset) {
  let col = 0;
  for (let i = lineStart; i < uptoOffset; i += 1) {
    col = text[i] === '\t' ? col + (4 - (col % 4)) : col + 1;
  }
  return col;
}

// A bounded, one-level heuristic for "the enclosing container's content column" (plan: "fence
// recognition is relative to the enclosing container's content column"; the same reference point
// governs the over-indentation halt below). Walks backward from the image's own line, over any
// run of blank lines, looking for the nearest ordered/unordered list-marker line — this release
// does not track full container nesting (a blockquote marker, a second list at a different
// indentation, or a genuinely closed-and-reopened list item all read the same as "still open"), so
// a marker line found this way is trusted without re-verifying it is still open at the image's
// position. Returns 0 (top-level) when none is found before a non-blank, non-marker line.
const LIST_MARKER_LINE_RE = /^([ \t]*)((?:[-*+])|(?:\d{1,9}[.)]))([ \t]+)/;

function containerContentColumn(text, lineStart) {
  let cursor = lineStart;
  while (cursor > 0) {
    const prevLineEnd = cursor - 1; // the '\n' terminating the previous line
    const prevLineStart = lineStartOf(text, prevLineEnd);
    const prevLine = text.slice(prevLineStart, prevLineEnd);
    const m = prevLine.match(LIST_MARKER_LINE_RE);
    if (m) {
      const indentCol = tabExpandedColumn(text, prevLineStart, prevLineStart + m[1].length);
      return indentCol + m[2].length + m[3].length;
    }
    if (prevLine.trim() === '') {
      cursor = prevLineStart;
      continue;
    }
    break;
  }
  return 0;
}

// The image-indentation halt: undecidable whenever a live image's OWN line reaches four or more
// columns past its container's content column — regardless of what precedes it. Deciding it
// correctly in general needs a real block parser's paragraph-continuation state (this release does
// not add one), so the rule is uniform in both directions: neither "indented code interrupting a
// paragraph" nor "an ordinary list continuation at exactly the content column" gets a special
// case — only the column difference is measured.
function isOverIndented(text, markerOffset) {
  const lineStart = lineStartOf(text, markerOffset);
  const imageColumn = tabExpandedColumn(text, lineStart, markerOffset);
  return imageColumn - containerContentColumn(text, lineStart) >= 4;
}

// A conservative CommonMark-shaped raw HTML open/close tag matcher — deliberately NOT a fixed tag
// list (an enumeration is the losing game the halt rule exists to end): any tagname (an ASCII
// letter, then letters/digits/hyphens) immediately followed by '>', a self-closing '/>', or
// whitespace is a tag, so a custom element like '<x-provenance-probe>' is caught the same as
// '<img>'. Case-insensitive (HTML tag names are ASCII case-insensitive) and 's' lets a tag split
// across a line break still match. An autolink's scheme/email is excluded by construction:
// '<https:' and '<user@' both fail immediately after the tagname-shaped prefix, because ':' and
// '@' are neither '>', '/', nor whitespace, so the match never completes.
const RAW_HTML_TAG_RE = /<\/?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?\/?>/gis;
const PROCESSING_INSTRUCTION_RE = /<\?[\s\S]*?\?>/g;

// The triggers that carry NO '![' at all and therefore cannot be caught by the raw-text accounting
// scan: any raw HTML tag, a processing instruction, and a reference definition. Detected over the
// RAW text unconditionally — no live-context qualifier, so one shown inside a fenced example still
// halts the chapter (the accepted cost the plan states explicitly).
function findRawTextTriggers(text) {
  const triggers = [];
  for (const m of text.matchAll(RAW_HTML_TAG_RE)) {
    triggers.push({ offset: m.index, construct: `raw HTML tag '${m[0]}'` });
  }
  for (const m of text.matchAll(PROCESSING_INSTRUCTION_RE)) {
    triggers.push({ offset: m.index, construct: 'processing instruction' });
  }
  let idx = text.indexOf(']:');
  while (idx !== -1) {
    triggers.push({ offset: idx, construct: 'reference definition' });
    idx = text.indexOf(']:', idx + 1);
  }
  return triggers;
}

function haltResult(construct, line) {
  return { ok: false, halt: { construct, line } };
}

/**
 * expectedAssets(profileLike, entry, chapterFile, chapterText, filenames, target) — the chapter's
 * embedded images, or a halt naming the first construct the bounded extractor cannot account for.
 * Calls buildEmbedCandidates itself (the one entry point; no caller may hand in a prebuilt map).
 *
 * Two views of `chapterText` drive the two separate jobs this contract needs (see the plan's
 * "completeness-or-halt" discussion for why one view cannot do both): RECOGNITION runs over a
 * STRIPPED view (inert fences/code-spans/comments blanked, so an image marker inside one is
 * invisible here even when its destination happens to byte-match a real candidate); ACCOUNTING
 * runs over the RAW text and requires every unescaped '![' found there to correspond to a
 * recognized position — one that fell inside an inert span, or that recognition could not resolve
 * into a valid inline image, is unaccounted and halts. Counting on the stripped view instead (what
 * every earlier revision of this feature did) is precisely the defect this closes: a '![' the
 * stripper blanks away is invisible to a stripped-view count, never unconsumed, so it silently
 * never gets a chance to halt.
 *
 * @param {ProfileLike} profileLike
 * @param {ChapterEntry} entry
 * @param {string} chapterFile
 * @param {string} chapterText
 * @param {string[]} filenames
 * @param {string} target
 * @returns {{ok: true, assets: Array<{key: string, absPath: string}>}
 *         | {ok: false, halt: {construct: string, line: number}}}
 */
export function expectedAssets(profileLike, entry, chapterFile, chapterText, filenames, target) {
  let candidates;
  try {
    candidates = buildEmbedCandidates(profileLike, entry, chapterFile, filenames, target);
  } catch (err) {
    if (err instanceof EmbedCandidateHalt) return haltResult(err.construct, 0);
    throw err;
  }

  const assetDir = chapterAssetDir(profileLike, entry);
  const liveMarkers = findImageMarkers(stripInertContexts(chapterText, { indentedRunIsCode: true }));
  const liveByOffset = new Map(liveMarkers.map((m) => [m.offset, m]));
  const rawMarkers = findImageMarkers(chapterText);
  const rawTriggers = findRawTextTriggers(chapterText);

  const events = [
    ...rawTriggers.map((t) => ({ offset: t.offset, type: 'trigger', construct: t.construct })),
    ...rawMarkers.map((m) => ({ offset: m.offset, type: 'marker', marker: m })),
  ].sort((a, b) => a.offset - b.offset);

  const assets = [];
  const seenKeys = new Set();

  for (const event of events) {
    if (event.type === 'trigger') {
      return haltResult(event.construct, lineNumberOf(chapterText, event.offset));
    }
    const marker = event.marker;
    const live = liveByOffset.get(marker.offset);
    if (live === undefined) {
      return haltResult('unaccounted image marker', lineNumberOf(chapterText, marker.offset));
    }
    if (live.kind === 'reference') {
      return haltResult('reference-style image', lineNumberOf(chapterText, marker.offset));
    }
    if (live.kind === 'unterminated') {
      return haltResult('unterminated image construct', lineNumberOf(chapterText, marker.offset));
    }
    if (isOverIndented(chapterText, marker.offset)) {
      return haltResult('over-indented image', lineNumberOf(chapterText, marker.offset));
    }
    const destination = parseMdLinkDestination(live.rawDestination);
    const f = candidates.get(destination);
    if (f === undefined) {
      return haltResult(`unmatched image destination '${destination}'`, lineNumberOf(chapterText, marker.offset));
    }
    if (!seenKeys.has(f)) {
      seenKeys.add(f);
      assets.push({ key: f, absPath: posixJoin(assetDir, f) });
    }
  }

  return { ok: true, assets };
}

// ---------------------------------------------------------------------------------------------
// [1.12.0] W2 preflight gates 1-4 — pure, exported, independently callable (W6 must run them
// itself against a bare entry set before deriving a single path — "a gate that runs on the write
// path but not on the read path secures neither"). Gate 3 is the one exception to "pure means no
// fs": physical containment fundamentally needs real filesystem state (lstat/readlink), so it
// takes those as an injected `deps` — chapter-paths.mjs still imports no node:fs itself, matching
// this module's established convention; the caller (capture-record.mjs) wires deps to the real fs
// seam, exactly as it already does for its own exported entrypoints.
// ---------------------------------------------------------------------------------------------

/**
 * Gate 1 — slug alphabet. The SAME regex validateGroups already uses for `group`
 * (manifest-discipline.md's published pattern for that sibling field) — `slug` itself is
 * documented only as "English kebab-case" with no regex and nothing enforcing it, so this gate
 * adopts the sibling field's already-published one rather than inventing a stricter reading.
 * Digits are in the class on purpose (the shipped suite uses slug: 'q1'). A non-string slug (a
 * bare number, say) fails outright — the same numeric-vs-string aliasing this gate exists to
 * remove at the source, rather than trying to out-normalize a filesystem later.
 *
 * @param {unknown} slug
 * @returns {boolean}
 */
export function isValidSlugSyntax(slug) {
  return typeof slug === 'string' && GROUP_PATTERN.test(slug);
}

/**
 * Gate 2 — canonical uniqueness. Compares CANONICAL chapterAssetDir() results, never raw tails —
 * deliberately independent of gate 1: once the alphabet gate passes, separator/case/Unicode/
 * trailing-dot aliases are unreachable, so exercising this only through the full preflight (gate 1
 * then gate 2) could never distinguish "canonicalizes" from "a raw Set", which is the false-kill
 * the plan warns against — call this directly with alphabet-violating entries to prove it. Returns
 * one entry per COLLIDING canonical path (empty when every entry derives a distinct directory) —
 * never a boolean, since the caller must name every offending entry in its halt text.
 *
 * @param {CaptureProfileLike} profileLike
 * @param {ChapterEntry[]} entries
 * @returns {Array<{canonicalPath: string, entries: ChapterEntry[]}>}
 */
export function findCanonicalPathCollisions(profileLike, entries) {
  const byPath = new Map();
  for (const entry of entries) {
    const canonicalPath = chapterAssetDir(profileLike, entry);
    if (!byPath.has(canonicalPath)) byPath.set(canonicalPath, []);
    byPath.get(canonicalPath).push(entry);
  }
  const collisions = [];
  for (const [canonicalPath, group] of byPath) {
    if (group.length > 1) collisions.push({ canonicalPath, entries: group });
  }
  return collisions;
}

const CONTAINMENT_MAX_HOPS = 40;

/**
 * Gate 3 — physical containment, no-follow, cycle-safe. Resolves `dir`'s path components ONE AT A
 * TIME using ONLY deps.lstat/deps.readlink — never a single opaque realpath call — so the seam
 * trace shows the granular component-wise walk a test can assert on directly. A symlink
 * component's target is substituted: an ABSOLUTE target replaces the resolved-so-far path
 * outright; a RELATIVE target is resolved against the symlink's OWN PARENT directory, never
 * against the symlink's own full path — the two differ by exactly one segment, and getting this
 * backwards is the discriminator the plan measures (a relative `../outside` target resolved
 * against a link's parent correctly lands OUTSIDE a shallow root, while the identical target
 * resolved against the link's own path incorrectly lands INSIDE it). After every substitution the
 * walk restarts from the beginning of the newly-combined path rather than trying to resume at a
 * computed offset — a `..` in the target can pop back through the parent segments themselves, so
 * "the first segment the target introduced" is not a stable index to resume at; re-inspecting an
 * already-verified prefix segment is harmless; skipping a genuinely new one is not. A bounded hop
 * count (incremented only when an ACTUAL symlink is resolved, never on a plain re-inspection)
 * halts a genuine cycle with zero further inspection — stronger than merely terminating, since
 * returning the unresolved candidate on overflow would let the write proceed instead. Containment
 * is checked AT THE END, component-wise against `rootDir`'s own resolved segments — never a string
 * prefix, which `out/assets-evil` would otherwise satisfy against `out/assets`. `deps.lstat` and
 * `deps.readlink` THROWING are each their own halt ('inspection-failed') — treating an inspection
 * error as "absent, therefore not a symlink" would proceed to resolve past it without ever having
 * established containment, which is the exact guarantee this gate sells.
 *
 * @param {string} rootDir  the physical root every resolved path must sit inside (e.g. capture.output_dir)
 * @param {string} dir  the candidate directory to resolve (e.g. chapterAssetDir(profileLike, entry))
 * @param {{lstat: (path: string) => {isSymbolicLink(): boolean}, readlink: (path: string) => string}} deps
 * @returns {{ok: true, resolved: string} | {ok: false, halt: {reason: 'escapes-root'|'cycle'|'inspection-failed', detail: string}}}
 */
export function resolvePhysicalContainment(rootDir, dir, deps) {
  const rootSegs = resolvedSegments(rootDir);
  let segs = resolvedSegments(dir);
  let absolute = isAbsolute(dir);
  let hops = 0;
  let cursor = 0;

  while (cursor < segs.length) {
    const candidatePath = formatPath(segs.slice(0, cursor + 1), absolute);
    let stat;
    try {
      stat = deps.lstat(candidatePath);
    } catch (err) {
      return {
        ok: false,
        halt: { reason: 'inspection-failed', detail: `lstat failed on '${candidatePath}': ${err.message}` },
      };
    }
    if (!stat.isSymbolicLink()) {
      cursor += 1;
      continue;
    }
    hops += 1;
    if (hops > CONTAINMENT_MAX_HOPS) {
      return {
        ok: false,
        halt: { reason: 'cycle', detail: `symlink chain exceeded ${CONTAINMENT_MAX_HOPS} hops resolving '${dir}'` },
      };
    }
    let target;
    try {
      target = deps.readlink(candidatePath);
    } catch (err) {
      return {
        ok: false,
        halt: { reason: 'inspection-failed', detail: `readlink failed on '${candidatePath}': ${err.message}` },
      };
    }
    const parentSegs = segs.slice(0, cursor); // the symlink's OWN PARENT — never its own path
    const remainder = segs.slice(cursor + 1); // segments still to walk AFTER this one
    if (isAbsolute(target)) {
      segs = normalizeSegments([...rawSegments(target), ...remainder], true);
      absolute = true;
    } else {
      segs = normalizeSegments([...parentSegs, ...rawSegments(target), ...remainder], absolute);
    }
    cursor = 0;
  }

  const resolved = formatPath(segs, absolute);
  const withinRoot =
    absolute === isAbsolute(rootDir) && segs.length >= rootSegs.length && rootSegs.every((seg, i) => segs[i] === seg);
  if (!withinRoot) {
    return {
      ok: false,
      halt: { reason: 'escapes-root', detail: `'${dir}' resolves to '${resolved}', outside '${rootDir}'` },
    };
  }
  return { ok: true, resolved };
}

/**
 * Gate 4 — pairwise PHYSICAL uniqueness, over already gate-3-resolved directories. Gates 2 and 3
 * alone still permit two chapters to collapse onto one physical directory via a planted
 * inside-root symlink (gate 3 is REQUIRED to accept an inside-root link, since adopters
 * legitimately arrange assets that way) — this is the cross-entry property neither of the other
 * two gates can see: canonical uniqueness compares lexical strings, and physical containment asks
 * only whether EACH resolved path, individually, sits inside the root. Groups the
 * CALLER-SUPPLIED resolved physical paths (gate 3's own output, never re-derived here) and returns
 * every group with more than one member. The caller re-runs this at W5 too, because a symlink can
 * be planted between W2 and W5.
 *
 * **Trust boundary, stated rather than enforced**: `resolvedEntries[i].resolved` is taken on
 * trust — this function cannot verify it actually came from resolvePhysicalContainment rather
 * than a hand-built string, the same way a recovery verdict's `expected` elsewhere in this plan is
 * "an optimistic-concurrency witness whose shape and sources are public, deliberately not a
 * capability". Re-deriving it here (accepting rootDir/deps and calling gate 3 itself per entry)
 * was considered and rejected: it would conflate two gates the plan keeps separate on purpose —
 * gate 4 would then need to surface BOTH "this entry individually escapes the root" and "these
 * entries collide physically" from one call, which is exactly the kind of merged responsibility
 * "four gates, because no one of them covers the others" argues against. The caller is therefore
 * responsible for actually running gate 3 first and passing its real output through unmodified.
 *
 * @param {Array<{entry: ChapterEntry, resolved: string}>} resolvedEntries  each `resolved` MUST be
 *   the real return value of resolvePhysicalContainment(rootDir, dir, deps).resolved for that
 *   entry's own directory — never hand-constructed (see the trust-boundary note above)
 * @returns {Array<{resolvedPath: string, entries: ChapterEntry[]}>}
 */
export function findPhysicalPathCollisions(resolvedEntries) {
  const byResolved = new Map();
  for (const item of resolvedEntries) {
    if (!byResolved.has(item.resolved)) byResolved.set(item.resolved, []);
    byResolved.get(item.resolved).push(item.entry);
  }
  const collisions = [];
  for (const [resolvedPath, entries] of byResolved) {
    if (entries.length > 1) collisions.push({ resolvedPath, entries });
  }
  return collisions;
}
