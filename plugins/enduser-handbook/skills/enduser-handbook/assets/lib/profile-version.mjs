// enduser-handbook asset — non-normative reference implementation. The normative contract for the
// profile_version pre-flight scan (the algorithm, the whitespace rule, and the honest invariant) lives
// in references/profile-validation.md. The base skill's Step 0 gate is Claude READING the profile
// against that contract; this file is one Node-stdlib implementation of it, not a requirement.
//
// profile-version.mjs — a pure, parse-safe column-0 `profile_version:` line-scan over RAW profile
// text. It is explicitly NOT a YAML parser: see references/profile-validation.md for the exact shape
// it recognizes and the threat model. readProfileVersion never throws for any input.
//
// Known out-of-scope class (references/profile-validation.md + issue #110): this scan does NO
// cross-line structural validation — no indentation-consistency check, no bracket/quote balance, no
// alias resolution. A file invalid for any such reason (unterminated `[`/`{` or `"`/`'`, an invalid
// block dedent — reachable via the `capture.command: |` block scalar — or an undefined `*alias`) but
// whose column-0 top-level shape is intact can still return `ok`. It never returns the wrong version
// (a real parser reads no different profile_version from these — it fails to parse); the error
// surfaces visibly at real profile-load time. Tracked in #110.

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

  // YAML forbids a tab anywhere in a line's BLOCK-INDENTATION run — a tab as the colon→value
  // separator (e.g. `profile_version:\t1`) is legal and must stay unaffected, so this only matches a
  // tab inside the LEADING whitespace run, never one that follows non-whitespace. Checked over every
  // line, not just column-0 ones, because the forbidden tab is typically several levels deep (a
  // nested key under a later top-level key) — well past what the column-0 shape allowlist below
  // inspects. Without this, a document a real YAML parser cannot load at all (Psych::SyntaxError)
  // would scan clean if profile_version itself happens to be spelled correctly.
  const TAB_INDENT = /^[ \t]*\t/;
  if (lines.some((line) => TAB_INDENT.test(line))) {
    return malformed('profile_version scan: tab character used for indentation (YAML forbids tabs in block indentation)');
  }

  // Step 4 — top-level shape ALLOWLIST. Every column-0 line must be blank, a comment, or a
  // snake_case top-level key; anything else halts (naming the line). No indented non-blank,
  // non-comment line may appear before the first top-level key — an orphan indent means the
  // document is not a top-level block mapping. This single rule is what rejects every counterexample
  // (multi-document markers, flow mappings, sequences, quoted keys, multi-line scalar continuations
  // reaching column 0) without enumerating each shape by name.
  let seenKey = false;
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (isBlank(line)) continue;
    if (isIndented(line)) {
      if (!seenKey && !isComment(line)) {
        return malformed(`profile_version scan: line ${i + 1}: indented line before any top-level key`);
      }
      continue; // nested structure under an already-seen key, or an indented comment
    }
    if (isCol0Comment(line)) continue;
    if (!TOP_LEVEL_KEY.test(line)) {
      return malformed(`profile_version scan: line ${i + 1}: not a top-level key`);
    }
    seenKey = true;
  }

  // Step 5 — count column-0 keys named exactly profile_version, and remember the first top-level key
  // name for step 6's first-key rule.
  const hits = [];
  let firstKey = null;
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (isBlank(line) || isIndented(line) || isCol0Comment(line)) continue;
    const match = line.match(TOP_LEVEL_KEY); // always matches here — step 4 already proved it
    if (firstKey === null) firstKey = match[1];
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
