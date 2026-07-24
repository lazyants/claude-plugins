// enduser-handbook capture asset — non-normative reference implementation for the Playwright
// reference case. The normative, engine-agnostic contract lives in
// references/manifest-discipline.md, references/publish-targets/obsidian-vault.md,
// references/publish-targets/static-md.md, and references/revalidation.md (the D1-D6 design).
// Engine-neutral: reused as-is by any engine's driver glue and by capture.example.spec.ts.
//
// chapter-paths.mjs — the pure, dependency-free (no node:fs, no node:path — path algebra is
// reimplemented below so the module never depends on the host platform's separator convention)
// group axis helper for the optional `group`/`group_title` manifest fields (issue #19). Every
// exported function is a total, side-effect-free predicate/transform over plain data (manifest
// entries, profile-shaped objects, index-file line arrays, chapter/spec text) so the whole
// group-axis contract is unit-testable (tests/chapter-paths.test.mjs) without touching a
// filesystem or a browser.
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
    const keyedByGroup = perGroupSlugs && entry.group !== undefined;
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
function findMarkdownLinkGroups(line) {
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

function stripInertContexts(text) {
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
      if (runLen >= 3 && isLineStart(text, i)) {
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
function parseMdLinkDestination(raw) {
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

// R5-F1/F2: the SHARED headings-form classifier — locateChapterLine's `indexForm` field and
// findContainer's non-heading branch key on the EXACT same logic, over the EXACT same sanitized
// view, so the two functions can never disagree about what kind of file they're looking at.
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
 * legitimately ends in `.md` and must never be folded).
 *
 * @param {string[]} indexLines
 * @param {string} expectedTarget
 * @param {{wikilink?: boolean}} [options]
 * @returns {{present: boolean, containerTitle: string|null, multiple: boolean, indexForm: 'headings'|'non-heading', matches: Array<{line: string, containerTitle: string|null}>}}
 */
export function locateChapterLine(indexLines, expectedTarget, options = {}) {
  const { wikilink = false } = options;
  const wanted = foldTargetForMatch(expectedTarget, wikilink);
  // R4-F2: sanitize the WHOLE index text (not line-by-line — an inert region can itself span
  // multiple lines) through the shared stripper BEFORE any per-line processing, so a row sitting
  // inside an HTML comment or a fenced code block can never report present:true (a false
  // completion — the wiring is declared done when it never actually happened). join/split on '\n'
  // round-trips exactly because stripInertContexts preserves every newline unmodified.
  const sanitizedLines = stripInertContexts(indexLines.join('\n')).split('\n');
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
      // output, and a reader must see the real file content, never a blanked stand-in.
      matches.push({ line: indexLines[index], containerTitle });
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
 * comment), is classified `'non-heading'` (manual-wiring territory — first-class non-heading
 * automation is a follow-up issue). Depth >= 2 is deliberate (F1): a GROUP CONTAINER is `##
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
  const wanted = String(groupTitle).trim();
  const sanitizedLines = stripInertContexts(indexLines.join('\n')).split('\n');
  if (classifyIndexForm(sanitizedLines) === 'non-heading') return { kind: 'non-heading' };
  const headings = collectContainerHeadings(sanitizedLines);

  const matches = headings.filter((h) => h.title === wanted);
  if (matches.length > 1) return { kind: 'multiple', matches };
  if (matches.length === 1) return { kind: 'single', location: matches[0] };
  return { kind: 'zero', headingDepth: headings[0].depth };
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

/**
 * manualMigrationChecklist(profileLike, oldEntry|null, newEntry|null, vaultRelChaptersDir) — the
 * per-delta-kind terminal-state FACT DESCRIPTORS the D6 convergence check verifies. This function
 * is pure and has no filesystem/index access, so it does not itself evaluate met/unmet — it
 * derives the EXPECTED VALUES (current derived paths, old derived paths, index targets,
 * capture-spec dir spellings) a caller checks the real world against. An entry untouched by the
 * delta (no kind under classifyEntryDelta) returns [].
 *
 * @param {{capture: {output_dir: string}, publish: {chapters_dir: string, index_file: string, wikilinks: boolean}}} profileLike
 * @param {object|null} oldEntry
 * @param {object|null} newEntry
 * @param {string} [vaultRelChaptersDir]  wikilinks mode only — threaded into every
 *   currentIndexExpectedTarget call this function makes (see its own JSDoc, §1a)
 * @returns {Array<object>} fact descriptors, each carrying a `kind` tag
 */
export function manualMigrationChecklist(profileLike, oldEntry, newEntry, vaultRelChaptersDir) {
  const kind = classifyEntryDelta(oldEntry, newEntry);
  if (kind === null) return [];

  if (kind === 'removal') {
    const oldChapterPath = chapterFullPath(profileLike, oldEntry);
    const oldAssetDir = chapterAssetDir(profileLike, oldEntry);
    return [
      { kind: 'old-chapter-path-gone', path: oldChapterPath },
      { kind: 'old-asset-dir-gone', path: oldAssetDir },
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
    return `  ${slug}: removed — delete ${oldChapterPath}, ${oldAssetDir}, and its index line (was under container '${trimmedTitle(oldEntry)}')`;
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
  let line = `  ${slug}: ${oldChapterPath} -> ${newChapterPath}; assets ${oldAssetDir} -> ${newAssetDir}${suffix}`;
  if (kind === 'group-and-title-change') {
    line += `; container title '${trimmedTitle(oldEntry)}' -> '${trimmedTitle(newEntry)}'`;
  }
  return line;
}

/**
 * The production halt-text formatter (D6 "Halt texts" — exact strings). `changes` is
 * `groupChanges(...).changes`; `checklists[i]` is `manualMigrationChecklist(profileLike,
 * changes[i].oldEntry, changes[i].newEntry)` (parallel arrays) — the checklist facts are where
 * the rendered derived paths come from, since this formatter itself takes no profileLike. With
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
