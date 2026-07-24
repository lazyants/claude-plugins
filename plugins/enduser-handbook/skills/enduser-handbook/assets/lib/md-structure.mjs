// enduser-handbook capture asset — non-normative reference implementation for the Playwright
// reference case. The normative, engine-agnostic contract lives in
// references/manifest-discipline.md, references/publish-targets/obsidian-vault.md,
// references/publish-targets/static-md.md, and references/revalidation.md (the D1-D6 design).
// Engine-neutral: reused as-is by any engine's driver glue and by capture.example.spec.ts.
//
// md-structure.mjs — a pure, dependency-free (no node:fs, no node:path) Markdown heading-tree
// parser plus a shared fence-aware masking primitive. #303: gives the node:test side a REAL
// structural resolver — which Markdown branch a given line actually belongs to — to back the shell
// suite's line-order `assert_line_before` heuristic floor in tests/reference-assets.test.sh with an
// authoritative structural proof.
//
// PORTED, NOT REINVENTED. Every fence/CRLF/tab/heading rule below is a 1:1 restatement of the awk
// `_section_contains` engine in tests/reference-assets.test.sh (the shell suite's own fence-aware
// section-boundary gate). Keeping the two engines rule-identical is deliberate: both hard-gate the
// SAME reference-doc files, so a divergence would let one pass what the other rejects. The rules,
// verbatim from that awk:
//   - leading indent counts SPACE characters only; a leading TAB reports indent 0 and leaves the
//     tab as the line's first char, which can never equal a fence marker — so a tab-led line never
//     opens or closes a fence (#257). Column-wise this is also always correct: one leading tab
//     already expands past the <=3-space fence threshold.
//   - a fence opener is `` ` ``/`~` run of length >= 3 at indent <= 3; a backtick opener's info
//     string may not itself contain a backtick (CommonMark), tildes are exempt.
//   - a fence closer is the SAME char, run length >= the opener's, at indent <= 3, with only
//     whitespace after the run.
//   - a fence is opaque: the opener line, every content line, and the closer line are all masked.
//   - CRLF is normalized (a trailing \r is stripped before every per-line decision).
//   - a heading is `/^#+/` at column 0 (no leading space) — exact whole-line binding, first match
//     wins, prefix does NOT bind (`## Target` never binds `## Target extra`); this mirrors the
//     awk's `line == heading && found_heading == 0`.
//   - a section runs until the next heading at depth <= its own (`hlevel <= level` closes it); a
//     deeper heading stays INSIDE the section.
//
// No title-keyed object index is built anywhere here — lookups are linear `Array.prototype.find`
// scans over raw heading text — so a heading literally titled `## constructor` / `## toString`
// cannot collide with `Object.prototype` (the prototype-chain regression class this repo already
// hit in tests/profile-schema-evaluator.test.mjs). The md-structure.test.mjs fixtures lock that in.

// ---------------------------------------------------------------------------------------------
// Fence primitives — private, line-based, mirroring the awk helpers of the same intent.
// ---------------------------------------------------------------------------------------------

// awk leading_spaces: count of leading SPACE chars only (never tabs).
function leadingSpaces(s) {
  let i = 0;
  while (i < s.length && s[i] === ' ') i += 1;
  return i;
}

// awk count_run: length of the run of `ch` starting at position 0.
function countRun(s, ch) {
  let i = 0;
  while (i < s.length && s[i] === ch) i += 1;
  return i;
}

// awk blank_from: true iff every char at `from` onward is a space or tab (a valid fence closer has
// only whitespace after its marker run).
function blankFrom(s, from) {
  for (let i = from; i < s.length; i += 1) {
    if (s[i] !== ' ' && s[i] !== '\t') return false;
  }
  return true;
}

// Length-preserving blank of a single (newline-free) line — every char position becomes a space,
// so a masked region keeps the exact character offsets and line breaks of the source. `\r` inside a
// masked line becomes a space too (offset preserved); the `\n` separators live between array
// elements and are restored by the join, never blanked.
function blankLine(line) {
  return ' '.repeat(line.length);
}

/**
 * maskFencedRegions(text) — the SHARED, exported fence-aware masking primitive. Blanks every
 * character inside a fenced code block (opener line, content, closer line) to a space while keeping
 * every other character and every line break exactly in place, so the result is character-offset-
 * and line-position-preserving. Both parseHeadings (below) and the #258 citation scanner (PR3, a
 * later change) consume THIS one function — there must be exactly one fence implementation in JS,
 * never a second copy inside the citation scanner.
 *
 * Line-based, matching the awk engine: only block-level ``` / ~~~ fences are recognized (never
 * inline code spans or HTML comments — the awk engine deliberately dropped comment-awareness, and a
 * 4-space-indented code block is treated as live text there too). Per-line fence state is
 * re-evaluated exactly as the awk does.
 *
 * @param {string} text
 * @returns {string}
 */
export function maskFencedRegions(text) {
  const lines = String(text).split('\n');
  let inFence = false;
  let fenceChar = '';
  let fenceLen = 0;
  const out = lines.map((line) => {
    // A trailing \r is stripped for the fence DECISION only (CRLF normalization) — the emitted
    // line keeps its original bytes unless it is masked.
    const stripped = line.replace(/\r$/, '');
    const indent = leadingSpaces(stripped);
    const rest = stripped.slice(indent);
    const fc = rest.charAt(0);

    if (inFence) {
      let isClose = false;
      if (indent <= 3 && fc === fenceChar) {
        const run = countRun(rest, fenceChar);
        if (run >= fenceLen && blankFrom(rest, run)) isClose = true;
      }
      if (isClose) inFence = false;
      return blankLine(line); // opaque: content AND the closer line are masked
    }

    if (indent <= 3 && (fc === '`' || fc === '~')) {
      const run = countRun(rest, fc);
      if (run >= 3) {
        const info = rest.slice(run);
        // CommonMark: a backtick fence's info string may not contain a backtick; tildes have no
        // such rule. Otherwise this is ordinary text, not a fence opener.
        if (fc !== '`' || !info.includes('`')) {
          inFence = true;
          fenceChar = fc;
          fenceLen = run;
          return blankLine(line); // the opener line is masked too
        }
      }
    }

    return line;
  });
  return out.join('\n');
}

// The masked lines as an array, each CR-stripped — the shared per-line view parseHeadings and
// sectionStatus both read, so they can never disagree about what is a heading or where a section
// body lies. Line N of this array is 1-based line (N+1) of the source; masking preserves the \n
// count, so these indices match a raw split('\n') of the same text position-for-position.
function maskedLines(text) {
  return maskFencedRegions(text)
    .split('\n')
    .map((line) => line.replace(/\r$/, ''));
}

const HEADING_RE = /^#+/;

/**
 * parseHeadings(text) — a flat, document-order list of every heading, each as
 * `{ raw, title, depth, line, bodyStart, bodyEndExclusive }`. ALL coordinates are 1-based line
 * numbers.
 *
 * - `raw` is the heading's exact source line (CR-stripped); `depth` is its leading `#` run length;
 *   `title` is `raw` with that run and the surrounding horizontal whitespace removed.
 * - `line` is the heading's own line; `bodyStart = line + 1`.
 * - `bodyEndExclusive` is the line of the next heading at depth <= this heading's depth, or
 *   `lineCount + 1` when none exists (the EOF case). The body is the HALF-OPEN interval
 *   `[bodyStart, bodyEndExclusive)`; membership is `n >= bodyStart && n < bodyEndExclusive`. This
 *   half-open contract is load-bearing: an inclusive `bodyEnd` would have dropped the last real
 *   body line before every boundary and at EOF.
 *
 * Headings inside fenced code blocks are not headings (maskFencedRegions blanks them first).
 *
 * @param {string} text
 * @returns {Array<{raw: string, title: string, depth: number, line: number, bodyStart: number, bodyEndExclusive: number}>}
 */
export function parseHeadings(text) {
  const lines = maskedLines(text);
  const lineCount = lines.length;
  const headings = [];
  for (let idx = 0; idx < lineCount; idx += 1) {
    const raw = lines[idx];
    const m = HEADING_RE.exec(raw);
    if (m === null) continue;
    const depth = m[0].length;
    const title = raw.slice(depth).replace(/^[ \t]+/, '').replace(/[ \t]+$/, '');
    const line = idx + 1;
    headings.push({ raw, title, depth, line, bodyStart: line + 1, bodyEndExclusive: 0 });
  }
  for (let i = 0; i < headings.length; i += 1) {
    let end = lineCount + 1;
    for (let j = i + 1; j < headings.length; j += 1) {
      if (headings[j].depth <= headings[i].depth) {
        end = headings[j].line;
        break;
      }
    }
    headings[i].bodyEndExclusive = end;
  }
  return headings;
}

/**
 * findOwner(headings, lineNumber) — the DEEPEST heading whose `[bodyStart, bodyEndExclusive)`
 * contains `lineNumber`, or `null` when the line precedes the first heading's body. With proper
 * Markdown nesting the deepest owner is also the most recent one (greatest `bodyStart`), so this
 * resolves a line to the single most specific section it belongs to — e.g. a line under a second
 * sibling H3 returns that H3, never the first; a line in an H4 returns the H4, not its ancestor H2.
 * A heading's OWN line belongs to its PARENT (a heading is not inside its own body).
 *
 * @param {Array<{bodyStart: number, bodyEndExclusive: number}>} headings
 * @param {number} lineNumber
 * @returns {object | null}
 */
export function findOwner(headings, lineNumber) {
  let owner = null;
  for (const h of headings) {
    if (lineNumber >= h.bodyStart && lineNumber < h.bodyEndExclusive) {
      if (owner === null || h.bodyStart > owner.bodyStart) owner = h;
    }
  }
  return owner;
}

/**
 * sectionStatus(text, headingRaw, needle) — the JS-side mirror of the bash 3-way
 * `_section_contains` engine (an independent implementation, cross-referenced here but not
 * code-shared — different languages). Binds `headingRaw` by EXACT whole-line match on its first
 * occurrence, then searches for `needle` across that heading's section body
 * `[bodyStart, bodyEndExclusive)` — the same span the awk's `in_section` covers, deeper nested
 * headings included, fenced content excluded (masked).
 *
 * Returns:
 *   - `'heading-absent'` — `headingRaw` never occurs (awk exit 2; #302's distinct third state).
 *   - `'needle-absent'`  — heading found, needle not in its section (awk exit 1). An empty needle
 *     also buckets here, matching the awk's `needle != ""` guard.
 *   - `'found'`          — heading found and needle present in its section (awk exit 0).
 *
 * @param {string} text
 * @param {string} headingRaw  the exact heading line, e.g. '## Vault root'
 * @param {string} needle
 * @returns {'found' | 'needle-absent' | 'heading-absent'}
 */
export function sectionStatus(text, headingRaw, needle) {
  const headings = parseHeadings(text);
  const target = headings.find((h) => h.raw === headingRaw);
  if (target === undefined) return 'heading-absent';
  if (needle === '') return 'needle-absent';
  const lines = maskedLines(text);
  for (let ln = target.bodyStart; ln < target.bodyEndExclusive; ln += 1) {
    const line = lines[ln - 1];
    if (line !== undefined && line.includes(needle)) return 'found';
  }
  return 'needle-absent';
}
