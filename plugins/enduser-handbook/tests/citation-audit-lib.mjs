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
// whole pass is O(document length) — verified via the regression test below, which asserts both the
// non-match AND that runtime doesn't grow with input size.
//
// Fence handling is NOT reimplemented here: maskFencedRegions is imported from the ONE JS fence
// engine in assets/lib/md-structure.mjs and reused, so a citation-shaped string inside a ``` fence is
// excluded exactly the way the heading parser excludes fenced headings. maskFencedRegions is
// character-offset- and line-position-preserving, so a match offset in the masked text is the same
// offset in the raw source — that is what makes the per-occurrence character offsets below reliable.

import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
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
 * - `offset` is the absolute character offset of that title's opening `"` — a true per-occurrence
 *   identity (no two distinct occurrences share a starting offset), which is what the offset-keyed
 *   allowlist needs to tell two same-title citations apart.
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

/**
 * auditText(text) — extract every citation and resolve each against `text`'s own heading list.
 * Each returned record extends the extractCitations record with:
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
  return extractCitations(text).map((c) => {
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
