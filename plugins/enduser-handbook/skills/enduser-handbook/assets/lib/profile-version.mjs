// enduser-handbook asset — non-normative reference implementation. The normative contract for the
// profile_version pre-flight scan (the algorithm, the whitespace rule, and the honest invariant) lives
// in references/profile-validation.md. The base skill's Step 0 gate is Claude READING the profile
// against that contract; this file is one Node-stdlib implementation of it, not a requirement.
//
// profile-version.mjs — a pure, parse-safe column-0 `profile_version:` line-scan over RAW profile
// text. It is explicitly NOT a YAML parser: see references/profile-validation.md for the exact shape
// it recognizes and the threat model. readProfileVersion never throws for any input.
//
// Cross-line structural validation (references/profile-validation.md + issue #110): as of v1.3.0,
// scanStructure below validates two of the three mechanisms #110 identified — an unterminated flow
// collection / quoted scalar (mechanism A), and an alias to an anchor that does not exist anywhere in
// the document (mechanism C). Mechanism B (an invalid dedent, incl. one reachable via the
// `capture.command: |` block scalar) remains OUT OF SCOPE and deferred: it requires modeling
// block-scalar content lines and full indentation, which risks exactly the mis-parse false-reject a
// hand-rolled scan must never commit (see scanStructure's doc comment for the opacity-first argument).
// A file invalid only for reason B but whose column-0 top-level shape is intact can still return
// `ok` — it never returns the wrong version (a real parser reads no different profile_version from
// these — it fails to parse); the error surfaces visibly at real profile-load time.

import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

/** The only profile_version this release understands. */
export const CURRENT_PROFILE_VERSION = 1;

/** Every profile_version readProfileVersion accepts as 'ok'. */
export const SUPPORTED_PROFILE_VERSIONS = [1];

/**
 * Extension point for a future cross-version migration: { [from]: { to, instructions } }. Empty —
 * only v1 ships, so no cross-version migration exists yet. See references/profile-validation.md.
 * @type {Record<number, { to: number, instructions: string }>}
 */
export const MIGRATIONS = {};

// Whitespace is ASCII space and tab, everywhere in this algorithm — deliberately never JavaScript's
// `\s` or `String#trim()`. Both treat U+00A0 (NBSP), U+FEFF and U+3000 as whitespace; YAML's
// whitespace is space and tab only. Using the JS-native classes would make this scan disagree with a
// real YAML parser about where a value ends (see references/profile-validation.md).
const isBlank = (line) => /^[ \t]*$/.test(line);
const isComment = (line) => /^[ \t]*#/.test(line);
const isCol0Comment = (line) => /^#/.test(line);
const isIndented = (line) => /^[ \t]/.test(line);
const TOP_LEVEL_KEY = /^([A-Za-z_][A-Za-z0-9_]*)[ \t]*:([ \t]|$)/;
const asciiTrim = (value) => value.replace(/^[ \t]+/, '').replace(/[ \t]+$/, '');

// Ruby/Psych treats a lone \r, U+0085 (NEL), U+2028 (LS) and U+2029 (PS) as line terminators, on top
// of the usual C0 control characters. A scanner that splits only on \n would read
// "# hidden<CR>profile_version: 2" as one comment line while Psych ends the comment at the CR and
// sees a live duplicate key. Rejecting the whole class is safer than re-deriving YAML's exact
// line-break set (same allowlist discipline as the top-level shape check below).
const BAD_CHAR = /[\r\u0085\u2028\u2029\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/;

function malformed(message) {
  return { status: 'malformed', version: null, message };
}

function malformedStructural(detail) {
  return malformed(`profile_version scan: structural: ${detail}`);
}

const isSpaceOrTab = (c) => c === ' ' || c === '\t';

// Leading-space count only (not `\s`/tab-inclusive). As of the #126 fix the tab-indentation check
// runs AFTER this scan (it consults this scan's opacity maps), so a line may still hold a tab in its
// leading run when countIndent sees it. Counting spaces alone then reads a leading tab as a shorter
// indent, which can only END a block/plain region at or before where a real parser would — the
// conservative, false-reject-free direction the whole scan is built on. The forbidden bare tab is
// rejected immediately after this scan returns.
function countIndent(line) {
  let n = 0;
  while (n < line.length && line[n] === ' ') n += 1;
  return n;
}

// Scans forward from `startCol` for the character that closes a quoted scalar opened by `quoteChar`,
// honoring the one escape rule each style has (`''` inside a single-quoted scalar; `\"`/`\\` inside a
// double-quoted one). Returns { closed: true, index } (the position just past the closer) or
// { closed: false } if the line ends first — the caller then carries `quote` into the next line.
function scanQuoteClose(line, startCol, quoteChar) {
  let col = startCol;
  const n = line.length;
  while (col < n) {
    const ch = line[col];
    if (quoteChar === "'") {
      if (ch === "'") {
        if (line[col + 1] === "'") { col += 2; continue; } // '' escape — still inside the scalar
        return { closed: true, index: col + 1 };
      }
      col += 1;
    } else {
      if (ch === '\\') { col += 2; continue; } // \x escapes the next char, including \" and \\
      if (ch === '"') return { closed: true, index: col + 1 };
      col += 1;
    }
  }
  return { closed: false };
}

// Char-lexes flow-collection content from `startCol` to end of line: comments ([ \t]# to EOL, or a
// bare `#` at true column 0) are skipped entirely; `[`/`{` open a nested collection and `]`/`}` close
// one (untyped, floored at 0 — a stray or mismatched closer only decrements, never flags); a quote or
// an alias is only ever opened AT a flow-node-start (right after `[`/`{`/`,`, or right after a flow
// map key's `:` — the colon needs no following space when it directly follows a closing quote, so a
// JSON-style `{"k":"v"}` value quote still opens). Anywhere else these same characters are literal
// plain-element text (never flagged). Returns the updated { flowDepth, quote, aliasUndefined }.
function lexFlowChars(line, startCol, flowDepthIn, docHasAnchor, nodeStartInitial) {
  let flowDepth = flowDepthIn;
  let nodeStart = nodeStartInitial;
  let justClosedQuote = false;
  let col = startCol;
  const n = line.length;
  while (col < n) {
    const ch = line[col];
    if (col === 0 && ch === '#') return { flowDepth, quote: null, aliasUndefined: false };
    if (isSpaceOrTab(ch) && line[col + 1] === '#') return { flowDepth, quote: null, aliasUndefined: false };
    if (isSpaceOrTab(ch)) { col += 1; continue; } // insignificant whitespace never clears node-start
    if (ch === '"' || ch === "'") {
      if (nodeStart) {
        const r = scanQuoteClose(line, col + 1, ch);
        if (!r.closed) return { flowDepth, quote: ch, aliasUndefined: false };
        col = r.index;
        nodeStart = false;
        justClosedQuote = true;
        continue;
      }
      col += 1;
      nodeStart = false;
      justClosedQuote = false;
      continue;
    }
    if (ch === '[' || ch === '{') {
      flowDepth += 1;
      col += 1;
      nodeStart = true;
      justClosedQuote = false;
      continue;
    }
    if (ch === ']' || ch === '}') {
      flowDepth = Math.max(0, flowDepth - 1);
      // The moment the OUTERMOST flow node closes, stop lexing this line entirely — valid YAML
      // cannot re-open flow content for the same value once it has closed, so anything left on the
      // line is either a comment (possibly with NO space before the `#`, e.g. `]#[` — a shape the
      // whitespace-gated comment check above does not otherwise recognize) or already a syntax error;
      // continuing to char-lex it risks treating comment text as live flow and never re-closing
      // (workflow-round false-reject: `note: [1]#[` — ruby loads `{note: [1]}` fine, this used to
      // return "unterminated flow collection"). Stopping earlier only produces FEWER flags, so it
      // cannot introduce a new false-reject.
      if (flowDepth === 0) return { flowDepth, quote: null, aliasUndefined: false };
      col += 1;
      nodeStart = false;
      justClosedQuote = false;
      continue;
    }
    if (ch === ',') {
      col += 1;
      nodeStart = true;
      justClosedQuote = false;
      continue;
    }
    if (ch === ':') {
      const next = line[col + 1];
      const isSeparator = justClosedQuote || next === undefined || isSpaceOrTab(next);
      col += 1;
      nodeStart = isSeparator;
      justClosedQuote = false;
      continue;
    }
    if (ch === '*') {
      if (nodeStart && !docHasAnchor) return { flowDepth, quote: null, aliasUndefined: true };
      col += 1;
      nodeStart = false;
      justClosedQuote = false;
      continue;
    }
    col += 1;
    nodeStart = false;
    justClosedQuote = false;
  }
  return { flowDepth, quote: null, aliasUndefined: false };
}

// Consumes leading `- ` sequence introducers from `indent`, tracking the INNERMOST dash column (the
// safe, conservative column for a later block/plain opacity threshold — see the two call sites below)
// alongside the node column (the position right after the last dash's introducer whitespace, where the
// key or bare value actually starts). A lone `-` at EOL is an empty sequence entry: its node is the
// next line, so nothing more is classified here.
function consumeSeqIntroducers(line, indent) {
  let col = indent;
  let lastDashCol = null;
  const n = line.length;
  while (col < n && line[col] === '-' && (col + 1 >= n || isSpaceOrTab(line[col + 1]))) {
    lastDashCol = col;
    col += 1;
    while (col < n && isSpaceOrTab(line[col])) col += 1;
    if (col >= n) return { emptySeq: true };
  }
  return { nodeCol: col, lastDashCol };
}

// Finds where a fresh node's VALUE begins, scanning right from `nodeCol`: the first `: ` (colon then
// space/tab) or a trailing `:` at EOL is the mapping separator (value starts after it, or the value is
// empty if only whitespace/a comment follows); a `:` not followed by whitespace/EOL is not a separator
// (`a:b` is one plain token) and scanning continues. Hitting a `[ \t]#` comment, or one of
// `[ { " ' | > *` BEFORE any separator, stops the search early and reports `nodeCol` itself as the
// value start — nodeCol's own character is never special in that case (a plain block-mapping key
// cannot start with one of those reserved indicators), so this is always the safe, opaque fallback.
function findValueStart(line, nodeCol) {
  const n = line.length;
  for (let col = nodeCol; col < n; col += 1) {
    const ch = line[col];
    if (ch === '#' && (col === nodeCol || isSpaceOrTab(line[col - 1]))) break;
    if (ch === '[' || ch === '{' || ch === '"' || ch === "'" || ch === '|' || ch === '>' || ch === '*') break;
    if (ch === ':') {
      const next = line[col + 1];
      if (next === undefined || isSpaceOrTab(next)) {
        let vs = col + 1;
        while (vs < n && isSpaceOrTab(line[vs])) vs += 1;
        if (vs >= n || line[vs] === '#') return { empty: true };
        return { valueStart: vs };
      }
    }
  }
  return { valueStart: nodeCol };
}

// Skips up to two space-separated node properties at `pos` — a `![^ \t]*` tag and/or a
// `&[^ \t,[\]{}]*` anchor, either order (each `&` here already satisfies the document-wide `docHasAnchor`
// gate, precomputed once in scanStructure) — and returns the position where the real value starts.
function skipNodeProperties(line, pos) {
  let col = pos;
  const n = line.length;
  for (let iter = 0; iter < 2; iter += 1) {
    const ch = line[col];
    if (ch === '!') {
      col += 1;
      while (col < n && !isSpaceOrTab(line[col])) col += 1;
    } else if (ch === '&') {
      col += 1;
      while (col < n && !(isSpaceOrTab(line[col]) || line[col] === ',' || line[col] === '[' || line[col] === ']' || line[col] === '{' || line[col] === '}')) col += 1;
    } else {
      break;
    }
    while (col < n && isSpaceOrTab(line[col])) col += 1;
  }
  return col;
}

// Classifies one line that is NOT inside an open quote / flow / block / plain region (case 5 of
// scanStructure's per-line dispatch). Returns one outcome the caller acts on: `noop` (comment, empty
// value, or an ignored alias — no state change), `aliasUndefined` (fatal), `block`/`plain` (opens that
// opaque region), or `flowOpenAt`/`quoteOpenAt` (a position to open flow-lexing / a quote scan from).
function classifyFreshLine(line, docHasAnchor) {
  if (isBlank(line)) return { noop: true };
  const indent = countIndent(line);
  if (line[indent] === '#') return { noop: true }; // whole line is a comment

  const seq = consumeSeqIntroducers(line, indent);
  if (seq.emptySeq) return { noop: true };
  const { nodeCol, lastDashCol } = seq;
  // The entry column used for BOTH plain.indent and a block's introducerCol: the innermost dash
  // column when this is a sequence entry (never the column past the dash — see the file's `- value`
  // fold discussion), else the key column. Smaller is always the safe direction: it widens the opacity
  // net rather than narrowing it (see scanStructure's doc comment).
  const entryCol = lastDashCol !== null ? lastDashCol : nodeCol;

  const sep = findValueStart(line, nodeCol);
  if (sep.empty) return { noop: true };

  const valueStart = skipNodeProperties(line, sep.valueStart);
  if (valueStart >= line.length) return { noop: true };

  const ch = line[valueStart];

  if (ch === '|' || ch === '>') {
    let p = valueStart + 1;
    while (p < line.length && (line[p] === '+' || line[p] === '-' || (line[p] >= '0' && line[p] <= '9'))) p += 1;
    const remainder = line.slice(p);
    if (/^[ \t]*(#.*)?$/.test(remainder)) return { block: { indent: entryCol } };
    return { plain: { indent: entryCol } }; // header-junk (e.g. "| pipe") falls through to plain
  }
  if (ch === '[' || ch === '{') return { flowOpenAt: valueStart };
  if (ch === '"' || ch === "'") return { quoteOpenAt: valueStart };
  if (ch === '*') {
    if (!docHasAnchor) return { aliasUndefined: true };
    return { noop: true }; // a real anchor may define it somewhere in the document
  }
  if (ch === '#') return { noop: true };
  return { plain: { indent: entryCol } };
}

/**
 * Cross-line structural scan (issue #110): two of the three mechanisms the issue identified,
 * provably false-reject-free. Mechanism A — an unterminated flow collection (`[`/`{` that never
 * closes) or an unterminated quoted scalar (`"`/`'` that never closes). Mechanism C — an alias
 * (`*name`) where the WHOLE document has zero `&` anchors anywhere, so the alias is guaranteed
 * unresolved. Mechanism B (an invalid dedent) is NOT covered — see the file-header comment.
 *
 * The model is opacity-first: at every line we are either inside an opaque region (an open plain
 * scalar, block scalar, quoted scalar, or flow collection) whose interior is never classified, or at
 * a fresh line where only tokens appearing INLINE after a real introducer are classified. Ambiguity
 * always resolves to "opaque / don't flag" — accepted false-negatives, never a false flag. This is
 * why the two flags above are safe: an inline-opened flow/quote that never closes cannot parse under
 * any real YAML reading, and with no anchor anywhere in the document every alias is unresolved.
 *
 * Pure, single forward pass, iterative (no recursion, so it never throws — not even on a
 * pathologically long "- - - …" line or deep flow nesting).
 *
 * @param {string[]} lines
 * @returns {{ status: 'malformed', version: null, message: string } | null} null means "nothing
 *   structural found here" — the document may still be invalid for a reason outside this scan's scope.
 */
export function scanStructure(lines) {
  return structuralScan(lines).verdict;
}

// The engine behind scanStructure, additionally exposing two per-line opacity maps. `opaqueAtStart[i]`
// — was this document already inside an open flow collection or quoted scalar the MOMENT line i began?
// readProfileVersion's Step 4 shape allowlist needs this: a flow/quote continuation is one of the few
// YAML shapes allowed to reach column 0 with no indentation at all (block/plain continuations can't —
// they must be indented past their introducer — so only flow/quote need this exemption).
// `blockPlainOpaqueAtStart[i]` — was line i inside an open block scalar or plain scalar the moment it
// began? readProfileVersion's tab-indentation check (#126) needs this: a tab in the leading run is
// legal only as scalar CONTENT, i.e. inside an opaque region — for a block/plain region that also
// requires the line to be space-indented into it (see the tab check for the exact rule).
// scanStructure's own frozen signature stays `(lines) => verdict|null`; this richer shape is internal.
function structuralScan(lines) {
  // An over-approximation of "the document defines an anchor somewhere", not a literal "&" count: a
  // real anchor introducer always sits at a token-start position (preceded by whitespace, a flow
  // indicator `[ { , :`, or the start of the line — always a non-word character), so excluding a "&"
  // that's immediately preceded by a word character (`R&D`, `AT&T`) can only NARROW the gate, never
  // miss a real anchor — still false-reject-free. Without this exclusion, a stray "&" anywhere in
  // ordinary prose (a description, a comment) disabled mechanism C's undefined-alias check for the
  // WHOLE document; it still over-triggers on a space-surrounded prose "&" (`you & me`) — an accepted
  // false-negative, same as any other "&" that isn't really an anchor.
  const docHasAnchor = lines.some((line) => /(?:^|[^\w])&/.test(line));
  let flowDepth = 0;
  let quote = null;
  let block = null;
  let plain = null;
  const opaqueAtStart = new Array(lines.length).fill(false);
  const blockPlainOpaqueAtStart = new Array(lines.length).fill(false);
  const withMaps = (verdict) => ({ verdict, opaqueAtStart, blockPlainOpaqueAtStart });
  const aliasFatal = () => withMaps(malformedStructural('alias to undefined anchor'));

  let i = 0;
  while (i < lines.length) {
    opaqueAtStart[i] = flowDepth > 0 || quote !== null;
    blockPlainOpaqueAtStart[i] = block !== null || plain !== null;
    const line = lines[i];

    if (quote !== null) {
      const r = scanQuoteClose(line, 0, quote);
      if (!r.closed) { i += 1; continue; }
      quote = null;
      if (flowDepth > 0) {
        const tail = lexFlowChars(line, r.index, flowDepth, docHasAnchor, false);
        flowDepth = tail.flowDepth;
        if (tail.aliasUndefined) return aliasFatal();
        quote = tail.quote;
      }
      i += 1;
      continue;
    }

    if (flowDepth > 0) {
      const tail = lexFlowChars(line, 0, flowDepth, docHasAnchor, false);
      flowDepth = tail.flowDepth;
      if (tail.aliasUndefined) return aliasFatal();
      quote = tail.quote;
      i += 1;
      continue;
    }

    if (block !== null) {
      if (isBlank(line) || countIndent(line) > block.indent) { i += 1; continue; }
      block = null; // dedent ends the block; reprocess this same line as a fresh line below
    }

    if (plain !== null) {
      if (isBlank(line) || countIndent(line) > plain.indent) { i += 1; continue; }
      plain = null; // dedent ends the fold; reprocess this same line as a fresh line below
    }

    const c = classifyFreshLine(line, docHasAnchor);
    if (c.noop) { i += 1; continue; }
    if (c.aliasUndefined) return aliasFatal();
    if (c.block) { block = c.block; i += 1; continue; }
    if (c.plain) { plain = c.plain; i += 1; continue; }
    if (c.quoteOpenAt !== undefined) {
      const ch = line[c.quoteOpenAt];
      const r = scanQuoteClose(line, c.quoteOpenAt + 1, ch);
      if (!r.closed) quote = ch; // else: closed on this line, nothing further is classified here
      i += 1;
      continue;
    }
    if (c.flowOpenAt !== undefined) {
      flowDepth = 1;
      const tail = lexFlowChars(line, c.flowOpenAt + 1, flowDepth, docHasAnchor, true);
      flowDepth = tail.flowDepth;
      if (tail.aliasUndefined) return aliasFatal();
      quote = tail.quote;
      i += 1;
      continue;
    }
    i += 1;
  }

  if (flowDepth > 0) return withMaps(malformedStructural('unterminated flow collection'));
  if (quote !== null) return withMaps(malformedStructural('unterminated quoted scalar'));
  return withMaps(null);
}

/**
 * Scan RAW profile text for the column-0, top-level `profile_version:` mapping key.
 *
 * This is a pre-flight VERSION READER, not a YAML validator — see references/profile-validation.md
 * for the exact recognized shape and the honest invariant it upholds. Never throws for any input.
 *
 * @param {unknown} rawText
 * @returns {{ status: 'ok'|'unsupported'|'missing'|'duplicate'|'malformed', version: number|null, message: string }}
 */
export function readProfileVersion(rawText) {
  // Step 1 — never throws, for any input.
  if (typeof rawText !== 'string') {
    return malformed('profile_version scan: input is not a string');
  }

  // Step 2 — strip a UTF-8 BOM ONLY at offset 0. A BOM elsewhere is an ordinary character.
  let text = rawText.charCodeAt(0) === 0xfeff ? rawText.slice(1) : rawText;

  // Step 3 — normalize CRLF, then fail closed on any other line terminator or control character
  // BEFORE splitting on '\n'.
  text = text.replace(/\r\n/g, '\n');
  if (BAD_CHAR.test(text)) {
    return malformed('profile_version scan: unsupported line terminator or control character');
  }
  const lines = text.split('\n');

  // The cross-line structural scan (mechanisms A + C; #110) runs its full forward pass FIRST — both
  // Step 4's column-0 shape allowlist and the tab check just below need its per-line opacity maps.
  // `opaqueAtStart[i]` says whether line i was already inside an open flow collection or quoted scalar
  // the moment it began. A flow/quote continuation is one of the few YAML shapes allowed to reach
  // column 0 with NO indentation at all (unlike a block/plain continuation, which must always be
  // indented past its introducer) — without this, Step 4 false-rejects a valid `key: [1,\n2]`-shaped
  // document. The VERDICT half of this result is consumed later, at Step 6.5, unchanged from before.
  const structural = structuralScan(lines);

  // YAML forbids a tab anywhere in a line's BLOCK-INDENTATION run — a tab as the colon→value
  // separator (e.g. `profile_version:\t1`) is legal, so TAB_INDENT only matches a tab inside the
  // LEADING whitespace run, never one that follows non-whitespace. But a tab in that run is legal when
  // it is scalar CONTENT rather than indentation (#126): inside an open flow/quote region
  // (`opaqueAtStart`), or inside an open block/plain region on a line that is itself space-indented
  // into that region (`blockPlainOpaqueAtStart[i] && line[0] === ' '`). Everything else — a bare-tab
  // line at document level, or a tab-led line that dedents out of a block/plain region — halts,
  // because a real YAML parser (Psych::SyntaxError) cannot load it. Checked over every line, not just
  // column-0 ones: the forbidden tab is typically several levels deep, well past what the column-0
  // shape allowlist below inspects.
  const TAB_INDENT = /^[ \t]*\t/;
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (!TAB_INDENT.test(line)) continue;
    if (structural.opaqueAtStart[i]) continue;
    if (structural.blockPlainOpaqueAtStart[i] && line[0] === ' ') continue;
    return malformed('profile_version scan: tab character used for indentation (YAML forbids tabs in block indentation)');
  }

  // Step 4 — top-level shape ALLOWLIST, run as an opacity-first document state machine. Every column-0
  // line must be blank, a comment, a leading `---` document-start, a `...` document-end, a snake_case
  // top-level key, an explicit `? snake_case` key (and its `: value` line), or a flow/quote
  // continuation flush to column 0; anything else halts (naming the line). No indented non-blank,
  // non-comment line may appear before the first top-level key. The recognizers below run only on
  // non-opaque lines — an opaque continuation is consumed first — so the interior of a flow/quote/
  // block/plain region is never mis-read as document structure. `firstKey` (the first top-level or
  // explicit key name, in document order, skipping opaque continuations) is captured here for Step 6.
  const DOC_START = /^---(?:[ \t]+#.*)?[ \t]*$/;
  const DOC_END = /^\.\.\.(?:[ \t]+#.*)?[ \t]*$/;
  const EXPLICIT_KEY = /^\?[ \t]+([A-Za-z_][A-Za-z0-9_]*)[ \t]*(?:#.*)?$/;
  const EXPLICIT_VALUE = /^:([ \t]|$)/;
  let seenKey = false;
  let terminated = false; // a `...` document-end marker has closed the document
  let sawLeadingStart = false; // a leading `---` document-start has been consumed
  let pendingExplicitValue = false; // an `? key` line still awaits its `: value` line
  let firstKey = null;
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (isBlank(line)) continue;
    if (isComment(line)) continue; // a comment at any indent, before or after the document
    if (terminated && !DOC_START.test(line) && !DOC_END.test(line)) {
      return malformed(`profile_version scan: line ${i + 1}: content after document-end marker`);
    }
    if (structural.opaqueAtStart[i]) {
      if (terminated) {
        return malformed(`profile_version scan: line ${i + 1}: content after document-end marker`);
      }
      continue; // a flow/quote continuation flush to column 0 — its interior is never classified
    }
    if (isIndented(line)) {
      if (!seenKey) {
        return malformed(`profile_version scan: line ${i + 1}: indented line before any top-level key`);
      }
      continue; // nested structure under an already-seen key
    }
    if (DOC_START.test(line)) {
      if (seenKey || sawLeadingStart) {
        return malformed(`profile_version scan: line ${i + 1}: unexpected '---' document-start marker (multiple documents)`);
      }
      sawLeadingStart = true;
      continue;
    }
    if (DOC_END.test(line)) {
      terminated = true;
      continue;
    }
    const explicitKey = line.match(EXPLICIT_KEY);
    if (explicitKey !== null) {
      if (explicitKey[1] === 'profile_version') {
        return malformed(`profile_version scan: line ${i + 1}: profile_version written as an explicit '? ' key is not supported`);
      }
      firstKey ??= explicitKey[1];
      pendingExplicitValue = true;
      continue;
    }
    if (EXPLICIT_VALUE.test(line)) {
      if (pendingExplicitValue) {
        pendingExplicitValue = false;
        continue; // the `: value` line completing a prior `? key`
      }
      return malformed(`profile_version scan: line ${i + 1}: unexpected ':' with no explicit '? ' key`);
    }
    const topLevelKey = line.match(TOP_LEVEL_KEY);
    if (topLevelKey !== null) {
      firstKey ??= topLevelKey[1];
      seenKey = true;
      pendingExplicitValue = false; // a prior `? key` with no `: value` was a null-valued entry (valid)
      continue;
    }
    return malformed(`profile_version scan: line ${i + 1}: not a top-level key`);
  }

  // Step 5 — count column-0 keys named exactly profile_version. `firstKey` was captured in Step 4's
  // state machine (which also recognizes an explicit `? key`); this pass only tallies the hits, and
  // skips any line that began inside an open flow/quote region so a key-shaped continuation is never
  // miscounted (Step 4 exempts those the same way). Marker/explicit-value lines never match
  // TOP_LEVEL_KEY, so they fall out here without special-casing.
  const hits = [];
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (isBlank(line) || isIndented(line) || isCol0Comment(line)) continue;
    if (structural.opaqueAtStart[i]) continue; // a flow/quote continuation — not a real key
    const match = line.match(TOP_LEVEL_KEY);
    if (match === null) continue;
    if (match[1] === 'profile_version') hits.push(i);
  }
  if (hits.length === 0) {
    return { status: 'missing', version: null, message: 'profile_version scan: no profile_version key found' };
  }
  if (hits.length >= 2) {
    // YAML last-wins is unsafe to guess at — fires regardless of whether the values agree.
    const detail = hits.map((i) => `line ${i + 1} (${asciiTrim(lines[i])})`).join(', ');
    return { status: 'duplicate', version: null, message: `profile_version scan: duplicate profile_version key: ${detail}` };
  }

  // Step 6 — first-key rule: the single hit must be the first top-level key. firstKey is guaranteed
  // non-null here: hits.length === 1 (the guards above returned for 0 and >= 2), so a top-level key
  // exists and firstKey was set.
  if (firstKey !== 'profile_version') {
    return {
      status: 'missing',
      version: null,
      message: 'profile_version scan: profile_version is not the first top-level key',
    };
  }

  const keyLine = hits[0];

  // Step 6.5 — the structural verdict computed above (mechanisms A + C; #110), consumed only now:
  // independent of where the profile_version key sits, so it's already fully computed, but sitting
  // AFTER the first-key rule and BEFORE Step 7 keeps `missing`/`duplicate` winning over a structural
  // verdict; see scanStructure's doc comment for the false-reject-free argument.
  if (structural.verdict !== null) return structural.verdict;

  // Step 7 — forward scan for a plain-scalar continuation, NOT a single-line peek (a blank line
  // between the key and its continuation would otherwise walk straight past it). Skip blank and
  // comment lines at ANY indent; halt on the first indented non-blank, non-comment line; stop at the
  // first column-0 key (scalar terminated) or EOF.
  for (let j = keyLine + 1; j < lines.length; j += 1) {
    const line = lines[j];
    if (isBlank(line) || isComment(line)) continue;
    if (isIndented(line)) {
      return malformed(`profile_version scan: line ${j + 1}: multi-line plain-scalar continuation`);
    }
    break;
  }

  // Step 8 — value. Strip a trailing ` #…` comment, then trim (ASCII whitespace only). A value
  // beginning with a quote halts rather than being tolerated — stripping a comment out of a quoted
  // string is ambiguous, so the schema/procedure document unquoted-integer as the only canonical form.
  const rawValue = lines[keyLine].slice(lines[keyLine].indexOf(':') + 1);
  const value = asciiTrim(rawValue.replace(/[ \t]+#.*$/, ''));
  if (value.startsWith('"') || value.startsWith("'")) {
    return malformed('profile_version scan: write the version as an unquoted integer, not a quoted string');
  }
  if (!/^\d+$/.test(value)) {
    return malformed(`profile_version scan: value is not an unquoted integer: ${JSON.stringify(value)}`);
  }
  // A YAML 1.1 parser reads a leading-zero integer as OCTAL (`010` → 8) and reads an integer beyond
  // JavaScript's safe range exactly (whereas Number.parseInt would round it). Either would let the
  // scan report a version different from what a real parser loads — the one defect this scan must
  // never commit — so both halt rather than guess. (`0` alone is fine: a real parser also reads 0.)
  if (value.length > 1 && value[0] === '0') {
    return malformed(`profile_version scan: ambiguous leading-zero integer (a YAML parser may read it as octal): ${JSON.stringify(value)}`);
  }
  if (BigInt(value) > BigInt(Number.MAX_SAFE_INTEGER)) {
    return malformed(`profile_version scan: integer too large to read exactly: ${JSON.stringify(value)}`);
  }
  const version = Number.parseInt(value, 10);
  if (!SUPPORTED_PROFILE_VERSIONS.includes(version)) {
    return {
      status: 'unsupported',
      version,
      message:
        `profile_version scan: unsupported profile_version ${version}. ` +
        `Supported: ${SUPPORTED_PROFILE_VERSIONS.join(', ')}. No cross-version migration exists yet — ` +
        'see references/profile-validation.md for the migration table.',
    };
  }
  return { status: 'ok', version, message: 'profile_version scan: ok' };
}

// ---- Optional CLI tail -------------------------------------------------------------------------
// A determinism aid for node-present / authoring contexts (e.g. /scaffold-profile). The base skill's
// Step 0 gate does NOT require node — it is Claude reading the profile per
// references/profile-validation.md. `node profile-version.mjs <path>` never lets a stack trace reach
// stdout/stderr: exit 0 = ok, 1 = a halting verdict (unsupported/missing/duplicate/malformed),
// 2 = usage or IO error (missing arg, unreadable path).
function runCli(argv) {
  const path = argv[2];
  if (!path) {
    process.stderr.write('usage: node profile-version.mjs <path-to-profile.yml>\n');
    return 2;
  }
  let raw;
  try {
    raw = readFileSync(path, 'utf8');
  } catch (err) {
    process.stderr.write(`profile-version: cannot read ${path}: ${err.code ?? err.message}\n`);
    return 2;
  }
  const result = readProfileVersion(raw);
  if (result.status === 'ok') {
    process.stdout.write(`${result.message}\n`);
    return 0;
  }
  process.stderr.write(`${result.message}\n`);
  return 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = runCli(process.argv);
}
