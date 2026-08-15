// Citation-direction lint library (#258) — test-only tooling, lives under tests/ (NOT assets/lib/),
// so it is exempt from the assets/lib normative-banner and .d.mts/.test pairing gates in
// reference-assets.test.sh (which scan $ASSETS/lib only). Same placement precedent as
// tests/profile-schema-evaluator.mjs.
//
// What it does: every reference doc that says a section is "above" or "below" a given point states a
// DIRECTION that must agree with the heading's real position in the file. This scanner extracts
// those "<quoted title>" <direction> claims, resolves each quoted title to a real heading in the
// SAME file, and lets citation-audit.test.mjs assert the stated direction matches the heading's
// actual line position. Two live wrong-direction bugs (obsidian-vault.md) are what motivated it.
//
// MATCHER DESIGN — verb-free, by hard-won necessity. Three rounds of codex plan review each found a
// new sentence template that a verb-anchored ("see ...") regex missed (parenthesized, unparenthesized,
// comma-before-direction, compound two-title, and finally quoted titles with NO introducing verb at
// all — e.g. `"Layout you produce" below`). The structural signal was never the verb; it is the
// PROXIMITY of a quoted title to a direction word: one-or-more quoted titles, each optionally
// separated by whitespace / a single [,;:] / "and", then a trailing direction word. A single-target
// citation is just the one-quote case; a compound "A" and "B" below is the multi-quote case, and
// every quoted title in a matched chain is exploded into its own record sharing the chain's direction.
//
// This is a DELIBERATE over-match, not a precision matcher. A quoted string that coincidentally
// precedes "above"/"below" for an unrelated reason still becomes a candidate — but a candidate only
// gets a direction assertion if its text EXACTLY matches a real heading title; every other candidate
// lands in the mechanically-enforced unresolved allowlist (see citation-audit.test.mjs), never a
// false pass or fail. Given three rounds of undercounting via verb-specific patterns, over-matching
// into a tracked allowlist is the safer failure mode than guessing sentence templates. Explicit scope
// boundary: a bare, unquoted prose reference ("the section above") is out of scope by construction —
// there is no delimited target string to resolve against a heading.
//
// ALGORITHM — one linear forward pass, not one monolithic backtracking regex (review-bot finding,
// 2026-07-24). An earlier revision matched the whole "one-or-more quotes + direction" shape with a
// single regex retried at every quote-start position via `matchAll`; a security review then found
// exponential backtracking in that regex's separator, and after that was fixed (one quantified
// alternation instead of two adjacent optional `\s*`s), the review-bot found the retry-from-every-
// quote-start SHAPE was still quadratic on an undirected run (each of the N quote-start positions
// re-scans up to the remaining N-i quotes trying to complete a match before failing). The fix here
// removes the retry-from-every-position shape entirely:
//   1. Find every quoted title ONCE via `QUOTED_TITLE_RE.matchAll` (a single linear pass — this
//      regex has no repeated-group shape, so it cannot itself be quadratic).
//   2. Walk that (much smaller) list of quote matches ONE time, greedily growing a "chain" of
//      adjacent quotes for as long as the gap between consecutive quotes is separator-only
//      (`GAP_IS_SEPARATOR_ONLY_RE`, tested only against the SHORT slice strictly between two already-
//      found offsets — never a rescan of already-chained quotes).
//   3. Check ONCE, at the chain's actual end, whether a direction word immediately follows
//      (`TRAILING_DIRECTION_RE`, anchored so it either matches right there or fails immediately).
// This is provably complete for this task, not just faster: if the gap between quote[k] and
// quote[k+1] were "above"/"below" text, chain growth would have STOPPED at k (that gap fails the
// separator-only test, since "above"/"below" isn't in the separator alternation) — so no interior
// quote inside a maximal chain can ever be a valid direction-word boundary, and checking only the
// chain's endpoint misses nothing a shorter internal sub-chain could have matched. Each quote is
// visited O(1) times for chain growth and each chain incurs one bounded direction check, so the
// whole pass is O(document length) — guarded by the ReDoS regression test in citation-audit.test.mjs,
// which asserts the non-match AND bounds runtime two ways: an absolute 2-second budget on the largest
// single measured run, and a 60x ceiling on how much 16x more input costs. Measured, not assumed:
// healthy tops out near 29x, and the retired quadratic matcher this test exists to catch produces
// 117x and 38.8 SECONDS at the same input — so the absolute bound is the decisive gate (19x margin)
// and the ratio is the early signal (2x). It does not assert constant runtime and could not; the pass
// is O(n), not O(1).
// Both are WALL-CLOCK bounds and can go falsely red on a loaded machine — #343's standing residual,
// mitigated in the test (nanosecond clock, median of five samples per size, median of five paired
// ratios) rather than eliminated. Read one red as "measure again on a quiet box", never as a proven
// regression, before touching this regex.
//
// Fence handling is NOT reimplemented here: maskFencedRegions is imported from the ONE JS fence
// engine in assets/lib/md-structure.mjs and reused, so a citation-shaped string inside a ``` fence is
// excluded exactly the way the heading parser excludes fenced headings. maskFencedRegions is
// character-offset- and line-position-preserving, so a match offset in the masked text is the same
// offset in the raw source — that is what makes the per-occurrence character offsets below reliable.
//
// IDENTITY vs POSITION (#342). `offset` is a POSITION: exact, unique, and invalidated by every edit
// anywhere earlier in the file. The allowlist in citation-audit.test.mjs used to be keyed on it, so a
// typo fix three paragraphs above a citation re-wrote every entry below it and buried any real change
// in ~40 lines of pure position shift. Each record therefore also carries a positional-drift-immune
// IDENTITY — `citationKey`: file + enclosing section (title AND `sectionNth`, which section of that
// title) + quoted title + direction + `nth`, the ordinal among citations in that same section
// repeating that same title. Both are kept: identity is what the allowlist pins, position is what the
// human diagnostic prints.
//
// The identity is injective BY CONSTRUCTION, which is what preserves the per-occurrence
// distinguishability the offset key was originally chosen for: `nth` is assigned by walking each
// section's citations in document order, so two otherwise-identical citations — same line, same
// title, same direction — still receive different ordinals and can never collapse into one entry.
//
// INJECTIVITY IS NOT THE WHOLE PROPERTY, and an earlier revision of this comment confused the two.
// It argued that repeated section titles were harmless because they "merge into one ordinal group and
// the ordinal separates the members" — true about injectivity, and wrong about what the allowlist is
// for. Merging makes two DIFFERENT sections one namespace, so an unresolved citation moving from the
// first `## Same` to the second keeps its whole key and moves invisibly; unresolved citations are
// direction-unchecked, so that entry is the only record that it exists at all. Hence `sectionNth`:
// which section of that title, counted among same-titled headings. Found by codex review, with a
// working probe; the fixture that pins it is in citation-audit.test.mjs.
//
// What it deliberately does NOT survive, stated rather than implied: moving a citation into a
// different section, and re-ordering two citations that share a section AND a title. Both are real
// structural changes to a citation's context, not position noise, and should be re-reviewed — which
// is exactly what a changed key forces. What it CANNOT see is a setext-underlined heading, because
// parseHeadings recognizes ATX only — an inherited limit of md-structure, not of this key.

import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  findOwner,
  maskFencedRegions,
  parseHeadings,
} from '../skills/enduser-handbook/assets/lib/md-structure.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));

// The skill root that owns the reference corpus. references/**/*.md + SKILL.md are the files a
// citation can live in; citations never cross file boundaries in this doc set, so each file resolves
// against its own heading list only.
export const SKILL_ROOT = join(HERE, '../skills/enduser-handbook');

// Every quoted title in the text, found ONCE via a single linear `matchAll` pass — `g` is required.
// This regex has no repetition-of-a-group shape (just a literal-quote-delimited run), so it cannot
// itself exhibit the retry-from-every-position cost the old monolithic span regex had.
const QUOTED_TITLE_RE = /"([^"]*)"/g;
// A gap between two ADJACENT quoted titles counts as "still the same citation span" only if it is
// ENTIRELY separator characters (whitespace / `[,;:]` / the word "and") — anchored both ends (`^...$`)
// so it either fully matches a SHORT slice (the text strictly between two already-found quote
// offsets) or fails immediately; there is nothing left to backtrack over either way.
const GAP_IS_SEPARATOR_ONLY_RE = /^(?:[\s,;:]|\band\b)*$/i;
// The direction word immediately following the LAST quote in a chain, anchored at the start of the
// (small, bounded) text right after that quote's closing `"`. `[\s,;:]|\band\b` is a single quantified
// alternation with disjoint first characters (whitespace/`,`/`;`/`:` vs literal `a`), so it is
// deterministic — it consumes the immediate separator run (typically a handful of characters) and
// then either matches "above"/"below" right there or fails outright; it never rescans.
const TRAILING_DIRECTION_RE = /^(?:[\s,;:]|\band\b)*(above|below)\b/i;

// Collapse internal whitespace runs (a title wrapped across a source line break picks up a newline
// plus continuation-line indent — confirmed to bite at static-md.md's "Relative links" citation) and
// trim, so a wrapped citation compares equal to its single-line heading title.
export function collapseWhitespace(s) {
  return s.replace(/\s+/g, ' ').trim();
}

// 1-based line number of every source offset, via a precomputed line-start table (binary search).
// Built from the RAW text; maskFencedRegions preserves \n positions, so masked offsets map through
// this same table unchanged.
export function buildLineTable(text) {
  const starts = [0];
  for (let i = 0; i < text.length; i += 1) {
    if (text[i] === '\n') starts.push(i + 1);
  }
  return starts;
}

export function offsetToLine(lineStarts, offset) {
  let lo = 0;
  let hi = lineStarts.length - 1;
  let ans = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (lineStarts[mid] <= offset) {
      ans = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return ans + 1;
}

/**
 * extractCitations(text) — every citation occurrence in `text`, fenced regions excluded. Returns one
 * record PER QUOTED TITLE (a compound span explodes into several), each:
 *   { offset, line, quotedRaw, quotedText, direction }
 * - `offset` is the absolute character offset of that title's opening `"`. It is exact and unique (no
 *   two distinct occurrences share a starting offset) but POSITIONAL, so it is the human diagnostic
 *   and the anchor for text surgery, never the allowlist key — see IDENTITY vs POSITION above.
 * - `quotedRaw` is the exact inner text (may contain a line break); `quotedText` is its
 *   whitespace-collapsed form, used for heading-title comparison; `direction` is lowercased.
 *
 * @param {string} text
 * @returns {Array<{offset: number, line: number, quotedRaw: string, quotedText: string, direction: 'above' | 'below'}>}
 */
export function extractCitations(text) {
  const masked = maskFencedRegions(text);
  const lineStarts = buildLineTable(text);
  const quotes = [...masked.matchAll(QUOTED_TITLE_RE)];
  const out = [];
  let i = 0;
  while (i < quotes.length) {
    // Grow the maximal chain of quotes[i..j] joined only by separator-only gaps — each comparison is
    // against the short slice strictly between two already-found quote offsets, so this loop visits
    // every quote O(1) times overall, never rescanning a quote already absorbed into the chain.
    let j = i;
    while (j + 1 < quotes.length) {
      const gapStart = quotes[j].index + quotes[j][0].length;
      const gapEnd = quotes[j + 1].index;
      if (!GAP_IS_SEPARATOR_ONLY_RE.test(masked.slice(gapStart, gapEnd))) break;
      j += 1;
    }
    const afterChainEnd = quotes[j].index + quotes[j][0].length;
    const dirMatch = TRAILING_DIRECTION_RE.exec(masked.slice(afterChainEnd));
    if (dirMatch) {
      const direction = dirMatch[1].toLowerCase();
      for (let k = i; k <= j; k += 1) {
        const offset = quotes[k].index;
        const quotedRaw = quotes[k][1];
        out.push({
          offset,
          line: offsetToLine(lineStarts, offset),
          quotedRaw,
          quotedText: collapseWhitespace(quotedRaw),
          direction,
        });
      }
    }
    // Whether or not this chain resolved to a citation, no sub-chain ending anywhere within [i, j]
    // can find a different, earlier direction boundary (see the ALGORITHM note above) — advance past
    // the whole chain rather than retrying from i+1, which is what keeps this pass linear.
    i = j + 1;
  }
  return out;
}

// Section title used for a citation that sits above the file's first heading. Spelled as prose, not
// as an empty string, so a pinned allowlist entry reads unambiguously instead of showing a blank.
export const PREAMBLE_SECTION = '(before the first heading)';
// ...and the ordinal that goes with it. Negative on purpose: a heading's ordinal among same-titled
// headings is always >= 0, so nothing a document can contain collides with the preamble's identity —
// including a heading whose title IS the sentinel string above.
export const PREAMBLE_SECTION_NTH = -1;

/**
 * sectionIdentity(headings, line) — {section, sectionNth}: the enclosing section's collapsed title
 * PLUS which section of that title it is, counted in document order among same-titled headings.
 *
 * The title alone is not an identity. Two sections can carry the same title in one file (identical
 * spelling, or differing only in whitespace, which collapses to the same string), and a citation that
 * moves from one to the other keeps every other key component — so without this ordinal the move is
 * invisible, and a citation can silently acquire a different context without any allowlist entry
 * changing. `sectionNth` is not positional in the sense #342 is about: inserting prose, or any
 * heading with a DIFFERENT title, does not move it. It moves only when a same-titled section is added
 * or removed before it, which is a structural change to exactly the thing this component identifies.
 *
 * @param {Array<{title: string, bodyStart: number, bodyEndExclusive: number}>} headings
 * @param {number} line
 * @returns {{section: string, sectionNth: number}}
 */
export function sectionIdentity(headings, line) {
  const owner = findOwner(headings, line);
  // The preamble is decided by the ABSENCE of an owner, never by its title matching the sentinel —
  // and it takes an ordinal no heading can ever produce. Both halves are needed: a document whose
  // first heading is literally titled `(before the first heading)` would otherwise hand its citations
  // the preamble's exact identity, which is the same aliasing sectionNth exists to prevent, one level
  // up. Deciding on `owner === null` alone does not close it; the ordinal is what does.
  if (owner === null) return { section: PREAMBLE_SECTION, sectionNth: PREAMBLE_SECTION_NTH };
  const section = collapseWhitespace(owner.title);
  const sameTitle = headings
    .filter((h) => collapseWhitespace(h.title) === section)
    .sort((a, b) => a.bodyStart - b.bodyStart);
  return { section, sectionNth: sameTitle.findIndex((h) => h.bodyStart === owner.bodyStart) };
}

/**
 * enclosingSection(headings, line) — the collapsed title of the section `line` is written in.
 *
 * Resolved by md-structure's own `findOwner` rather than by a third copy of that logic living here:
 * the same reuse rule the fence mask follows above, and the module that owns document structure owns
 * this question too. `findOwner` returns the DEEPEST heading whose body contains the line, which for
 * properly nested Markdown is also the nearest heading above it — measured across all 94 corpus
 * citations, it agrees with a nearest-heading-above scan on every one.
 *
 * Two boundary answers follow from findOwner's contract, and are pinned by fixtures in
 * citation-audit.test.mjs rather than left to be rediscovered:
 *   - a citation that precedes the first heading's body has no owner -> PREAMBLE_SECTION;
 *   - a citation written INTO a heading's own line belongs to that heading's PARENT (or, for a
 *     top-level heading, to PREAMBLE_SECTION), because a heading is not inside its own body. No
 *     corpus citation does this today.
 *
 * @param {Array<{title: string, bodyStart: number, bodyEndExclusive: number}>} headings
 * @param {number} line
 * @returns {string}
 */
export function enclosingSection(headings, line) {
  const owner = findOwner(headings, line);
  return owner === null ? PREAMBLE_SECTION : collapseWhitespace(owner.title);
}

/**
 * citationKey(record) — the positional-drift-immune identity of one citation occurrence (#342):
 * file + enclosing section (title AND which section of that title) + quoted title + direction + the
 * `nth` ordinal within that group. NUL-joined because every component is free-form document text and
 * any printable separator could occur inside one. `file` is absent on records from auditText (a
 * single anonymous document) and empty there; auditCorpus records always carry it.
 *
 * @param {{file?: string, section: string, sectionNth: number, quotedText: string, direction: string, nth: number}} record
 * @returns {string}
 */
export function citationKey(record) {
  return [
    record.file ?? '',
    record.section,
    record.sectionNth,
    record.quotedText,
    record.direction,
    record.nth,
  ].join('\0');
}

// The ordinal group — deliberately NOT everything in citationKey minus the ordinal: `direction` is
// excluded, so two citations of one title in one section are numbered as a pair regardless of which
// way each points. That is what makes SWAPPING their directions change the key SET rather than merely
// reorder it, which is the hard constraint the offset key was originally chosen to satisfy: with
// direction inside the group, both swapped records would keep `nth: 0` and the set comparison could
// not see the swap at all. Per-file by construction — auditText only ever sees one document.
function ordinalGroup(record) {
  return [record.section, record.sectionNth, record.quotedText].join('\0');
}

/**
 * auditText(text) — extract every citation and resolve each against `text`'s own heading list.
 * Each returned record extends the extractCitations record with:
 *   - section, sectionNth: the enclosing section's identity (see sectionIdentity)
 *   - nth: 0-based ordinal among this document's citations sharing (section, sectionNth, quotedText),
 *     assigned in document order — the tie-break that keeps citationKey injective
 *   - status: 'resolved' | 'unresolved' | 'ambiguous'
 *       resolved   = exactly one heading title equals the collapsed quoted text
 *       unresolved = zero matching headings (an over-match or a citation to a non-heading)
 *       ambiguous  = two or more headings share the title (must hard-fail — same defect class as the
 *                    #303 decoy issue: never silently pick one)
 *   - matchLines: the heading line(s) that matched (for reporting ambiguous / resolved)
 *   - heading, expectedDirection, directionOk: resolved records only. expectedDirection is 'above'
 *     when the heading sits before the citation line, 'below' when after ('same' is degenerate and
 *     never direction-correct). directionOk is (expectedDirection === direction).
 *
 * @param {string} text
 * @returns {Array<object>}
 */
export function auditText(text) {
  const headings = parseHeadings(text);
  const records = extractCitations(text).map((raw) => {
    const c = { ...raw, ...sectionIdentity(headings, raw.line) };
    const matches = headings.filter((h) => collapseWhitespace(h.title) === c.quotedText);
    if (matches.length === 0) return { ...c, status: 'unresolved', matchLines: [] };
    if (matches.length >= 2) {
      return { ...c, status: 'ambiguous', matchLines: matches.map((h) => h.line) };
    }
    const heading = matches[0];
    let expectedDirection;
    if (heading.line < c.line) expectedDirection = 'above';
    else if (heading.line > c.line) expectedDirection = 'below';
    else expectedDirection = 'same';
    return {
      ...c,
      status: 'resolved',
      matchLines: [heading.line],
      heading,
      expectedDirection,
      directionOk: expectedDirection === c.direction,
    };
  });
  // Ordinals last, over the finished records: extractCitations returns document order, so walking the
  // list once and counting per group numbers each occurrence by its position among its own siblings.
  const seen = new Map();
  for (const record of records) {
    const group = ordinalGroup(record);
    const nth = seen.get(group) ?? 0;
    seen.set(group, nth + 1);
    record.nth = nth;
  }
  return records;
}

// Recursively list every *.md under `dir` (posix-style relative paths from `dir`), plus discovery of
// the corpus below. Kept tiny and dependency-free, matching this plugin's no-node_modules stance.
function listMarkdown(dir, prefix = '') {
  const found = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isDirectory()) {
      found.push(...listMarkdown(join(dir, entry.name), rel));
    } else if (entry.isFile() && entry.name.endsWith('.md')) {
      found.push(rel);
    }
  }
  return found;
}

/**
 * corpusFiles(root = SKILL_ROOT) — the sorted list of reference files a citation may appear in:
 * every references/**\/*.md plus the top-level SKILL.md. Sorted for a deterministic total and
 * allowlist ordering.
 *
 * @param {string} root
 * @returns {string[]} posix-relative paths from `root`
 */
export function corpusFiles(root = SKILL_ROOT) {
  const files = listMarkdown(join(root, 'references'), 'references');
  files.push('SKILL.md');
  return files.sort();
}

/**
 * auditCorpus(root = SKILL_ROOT) — auditText over every corpus file, each record tagged with its
 * `file` (posix-relative path). The full flat record list the test's guards run against.
 *
 * @param {string} root
 * @returns {Array<object>}
 */
export function auditCorpus(root = SKILL_ROOT) {
  const records = [];
  for (const file of corpusFiles(root)) {
    const text = readFileSync(join(root, file), 'utf8');
    for (const rec of auditText(text)) records.push({ file, ...rec });
  }
  return records;
}

// The BASIC-PLANE characters that are invisible (or render as something other than themselves) in a
// source file: C0 controls, DEL + C1 including NEL, soft hyphen, the Arabic letter mark, the
// zero-width and directional-mark block, the line/paragraph separators, the bidi embedding+override
// block, word joiner and invisible operators, the bidi isolates, and ZWNBSP/BOM. Spelled entirely in
// `\u` escapes: writing the characters themselves is how this class would silently acquire the very
// payload it exists to neutralize, and a reviewer could not see the difference.
//
// Two reviewers independently measured the same boundary, so it is stated instead of implied: the
// class is BMP-only, and the supplementary-plane invisibles — Unicode tag characters (U+E0000-E007F,
// the ASCII-smuggling block), the variation selectors and their supplement — pass through raw.
// Covering them needs the `u` flag and the variable-width `\u{...}` escape form, i.e. two escape
// spellings in one emitter, which is more machinery than the threat here earns: the trust boundary is
// contributors with commit access, and the value this pass actually adds is the zero-width and bidi
// family that JS `\s` does NOT strip (collapseWhitespace already removes everything `\s` covers).
// Widen it if that boundary ever changes; do not widen it because a scanner reports the gap.
const INVISIBLE_IN_SOURCE_RE =
  /[\u0000-\u001F\u007F-\u009F\u00AD\u061C\u200B-\u200F\u2028-\u2029\u202A-\u202E\u2060-\u2064\u2066-\u2069\uFEFF]/g;

// A JS single-quoted string literal for `s`. Backslashes first, then quotes, so an already-escaped
// backslash is not double-counted — and the invisible-character pass runs LAST for the same reason
// (it emits `\u` escapes, which an earlier backslash pass would double-escape). Matches the spelling
// the pinned table in citation-audit.test.mjs uses, which is what lets a regenerated block be pasted
// in verbatim.
//
// The invisible pass is defense-in-depth for the REVIEW, not for execution: nothing here is eval'd,
// and a quote or backslash was already neutralized above. What it prevents is an allowlist entry that
// renders to a human exactly like a different entry — a bidi override or a zero-width space inside a
// heading title would otherwise be pasted into the pinned table invisibly, and the table's whole job
// is to be a record a human can read and trust. Every such character comes back as an ASCII `\uXXXX`
// escape, which is still the same string to the runtime and a visible difference to the reader.
function jsQuote(s) {
  const escaped = s
    .replace(/\\/g, '\\\\')
    .replace(/'/g, "\\'")
    .replace(INVISIBLE_IN_SOURCE_RE, (c) => `\\u${c.codePointAt(0).toString(16).padStart(4, '0')}`);
  return `'${escaped}'`;
}

/**
 * formatUnresolvedAllowlist(records) — the EXPECTED_UNRESOLVED body of citation-audit.test.mjs,
 * regenerated from an audit (#342). One entry per line, in corpus document order, each carrying only
 * the citationKey components — so a real change is one changed line and there is nothing
 * position-derived left to churn. The output is pasted between that array's brackets verbatim; a test
 * in citation-audit.test.mjs asserts the regenerated text equals the block that file actually ships,
 * so "run the command, paste, commit" is a closed loop rather than a hand-transcription.
 *
 * @param {Array<object>} records — an auditCorpus() result (any status; unresolved is selected here)
 * @returns {string} newline-joined source lines, no trailing newline
 */
export function formatUnresolvedAllowlist(records) {
  return records
    .filter((r) => r.status === 'unresolved')
    .map(
      (r) =>
        `  { file: ${jsQuote(r.file)}, section: ${jsQuote(r.section)}, sectionNth: ${r.sectionNth}, ` +
        `quotedText: ${jsQuote(r.quotedText)}, direction: ${jsQuote(r.direction)}, nth: ${r.nth} },`,
    )
    .join('\n');
}
