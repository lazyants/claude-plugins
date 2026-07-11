// Unit tests for the profile_version pre-flight scan. Zero deps — runs under Node's built-in test
// runner: `node --test profile-version.test.mjs`.
//
// The fixtures below are the fixed, curated verdicts from a differential proof harness that ran the
// scan against a REAL YAML parser (Ruby/Psych) over 39 hand-written cases + 12 further edge cases:
// 0 invariant violations (every 'ok' verdict agreed with Ruby on the exact integer; every
// counterexample halted). The Ruby oracle itself does not ship — only these fixtures and their
// verdicts do. See references/profile-validation.md for the algorithm and the honest invariant it
// upholds.
//
// Special characters are built with String.fromCharCode/fromCodePoint rather than \u escapes in
// string/regex literals, so the exact code point under test is unambiguous in source and in diffs.

import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  readProfileVersion,
  scanStructure,
  CURRENT_PROFILE_VERSION,
  SUPPORTED_PROFILE_VERSIONS,
  MIGRATIONS,
} from '../skills/enduser-handbook/assets/lib/profile-version.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const LIB_PATH = join(HERE, '../skills/enduser-handbook/assets/lib/profile-version.mjs');
const EXAMPLE_PATH = join(HERE, '../skills/enduser-handbook/assets/handbook.profile.example.yml');

// Unicode code points, built without \u escapes in source (see file header).
const BOM = String.fromCharCode(0xfeff);
const NEL = String.fromCharCode(0x0085);
const LS = String.fromCharCode(0x2028);
const PS = String.fromCharCode(0x2029);
const VT = String.fromCharCode(0x000b);
const ASTRAL = String.fromCodePoint(0x1f600); // an astral (surrogate-pair) character

// ---- exported constants -------------------------------------------------------------------------

test('exported constants', () => {
  assert.equal(CURRENT_PROFILE_VERSION, 1);
  assert.deepEqual(SUPPORTED_PROFILE_VERSIONS, [1]);
  assert.deepEqual(MIGRATIONS, {}); // no cross-version migration exists yet
});

// ---- the real shipped file (not a synthetic copy) ------------------------------------------------

test('the real shipped handbook.profile.example.yml scans ok, version 1', () => {
  const raw = readFileSync(EXAMPLE_PATH, 'utf8');
  const result = readProfileVersion(raw);
  assert.equal(result.status, 'ok');
  assert.equal(result.version, 1);
});

// ---- the seven counterexamples that killed earlier drafts (+ two more of the same shape) --------
// Every one of these is a case where a naive scan says "ok" but a real YAML parser reads a DIFFERENT
// version, an invalid document, or a value that is not the version at all. All must halt.

test('counterexample 1: multi-document — first doc declares a different version', () => {
  const r = readProfileVersion('---\n{ profile_version: 2 }\n---\nprofile_version: 1\n');
  assert.equal(r.status, 'malformed');
});

test('counterexample 2: quoted duplicate key — real parser reads the LATER value', () => {
  const r = readProfileVersion('profile_version: 1\n"profile_version": 2\n');
  assert.equal(r.status, 'malformed');
});

test('counterexample 3: indented plain-scalar continuation — real value is the string "1 trailing"', () => {
  const r = readProfileVersion('profile_version: 1\n  trailing\n');
  assert.equal(r.status, 'malformed');
});

test('counterexample 4: multi-line flow scalar hides a quoted duplicate key', () => {
  const r = readProfileVersion('foo: "bar\nprofile_version: 1\nbaz"\n"profile_version": 2\n');
  assert.equal(r.status, 'malformed');
});

test('counterexample 5: escaped quoted key decodes to the same key, later wins', () => {
  const r = readProfileVersion('profile_version: 1\n"profile\\u005fversion": 2\n');
  assert.equal(r.status, 'malformed');
});

test('counterexample 6: trailing invalid YAML — a real parser raises a syntax error', () => {
  const r = readProfileVersion('profile_version: 1\n:\n');
  assert.equal(r.status, 'malformed');
});

test('counterexample 7: blank-line continuation — real value is the string "1\\n2"', () => {
  const r = readProfileVersion('profile_version: 1\n\n  2\n');
  assert.equal(r.status, 'malformed');
});

test('counterexample 7b: blank-then-space-line continuation, same failure shape', () => {
  const r = readProfileVersion('profile_version: 1\n \n  more\n');
  assert.equal(r.status, 'malformed');
});

test('counterexample 8: BOM trailing the value — real value is the string "1<BOM>"', () => {
  const r = readProfileVersion('profile_version: 1' + BOM + '\n');
  assert.equal(r.status, 'malformed');
});

test('counterexample 9: NBSP before a hash is not a YAML comment separator', () => {
  const r = readProfileVersion('profile_version: 1' + String.fromCharCode(0x00a0) + '# comment\n');
  assert.equal(r.status, 'malformed');
});

test('counterexample 10: ideographic space is not YAML whitespace', () => {
  const r = readProfileVersion('profile_version: 1' + String.fromCharCode(0x3000) + '\n');
  assert.equal(r.status, 'malformed');
});

// ---- hidden line-terminator counterexamples (four shapes Ruby/Psych splits on, \n-only misses) --

for (const [label, sep] of [
  ['lone CR', '\r'],
  ['NEL (U+0085)', NEL],
  ['LS (U+2028)', LS],
  ['PS (U+2029)', PS],
  ['VT (U+000B)', VT],
]) {
  test(`hidden line terminator (${label}) hides a live duplicate key — halts fail-closed`, () => {
    const raw = 'profile_version: 1\n# hidden' + sep + 'profile_version: 2\n';
    const r = readProfileVersion(raw);
    assert.equal(r.status, 'malformed');
  });
}

// ---- tab-in-block-indentation counterexamples (HIGH bug, codex + lead confirmed) -----------------
// YAML forbids a tab anywhere in a line's block-indentation run. A scanner that only inspects
// column-0 shape (step 4) misses a forbidden tab several levels deep, so all four documents below
// scanned ok/1 before the dedicated guard while Ruby/Psych RAISES Psych::SyntaxError on every one —
// independently confirmed via `ruby -ryaml` (all 4 raised; the control parsed pv=1 cleanly).

test('tab-indented child of a later (non-profile_version) key halts', () => {
  const r = readProfileVersion('profile_version: 1\nfoo:\n\tbar: 1\n');
  assert.equal(r.status, 'malformed');
  assert.match(r.message, /tab character used for indentation/);
});

test('a tab-only "blank" line after the key halts (YAML: not actually blank)', () => {
  const r = readProfileVersion('profile_version: 1\n\t\nfoo: bar\n');
  assert.equal(r.status, 'malformed');
});

test('a forbidden tab four levels deep in an otherwise profile-shaped document halts', () => {
  const r = readProfileVersion(
    'profile_version: 1\nstack:\n  backend:\n    type: laravel\n\tapi_url_prefix: "/api/v1"\n',
  );
  assert.equal(r.status, 'malformed');
});

test('a tab-only "blank" line BEFORE the key halts', () => {
  const r = readProfileVersion('\t\nprofile_version: 1\n');
  assert.equal(r.status, 'malformed');
});

test('CONTROL: a tab as the colon-to-value separator is legal YAML and stays ok', () => {
  // The guard matches a tab only inside the LEADING whitespace run — this line has no leading
  // whitespace at all (it starts with "profile_version"), so it must NOT trip the new check.
  const r = readProfileVersion('profile_version:\t1\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

// ---- shapes that must stay ok -----------------------------------------------------------------

test('canonical minimal profile', () => {
  const r = readProfileVersion('profile_version: 1\nlanguage:\n  code: de\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('canonical + trailing inline comment (the real shipped shape)', () => {
  const r = readProfileVersion('profile_version: 1   # base skill refuses unknown versions\nlanguage:\n  code: de\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('comment preamble before the key', () => {
  const r = readProfileVersion('# handbook profile\nprofile_version: 1\nlanguage:\n  code: de\n');
  assert.equal(r.status, 'ok');
});

test('a comment line containing a colon does not confuse the shape allowlist', () => {
  const r = readProfileVersion('# note: colon\nprofile_version: 1\n');
  assert.equal(r.status, 'ok');
});

test('a blank line before the next top-level key', () => {
  const r = readProfileVersion('profile_version: 1  # c\n\nlanguage:\n  code: de\n');
  assert.equal(r.status, 'ok');
});

test('an indented comment after the key is not a continuation', () => {
  const r = readProfileVersion('profile_version: 1\n  # a comment\nlanguage:\n  code: de\n');
  assert.equal(r.status, 'ok');
});

test('a UTF-8 BOM at offset 0 is stripped', () => {
  const r = readProfileVersion(BOM + 'profile_version: 1\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('CRLF line endings are normalized', () => {
  const r = readProfileVersion('# c\r\nprofile_version: 1\r\nfoo: bar\r\n');
  assert.equal(r.status, 'ok');
});

test('trailing ASCII spaces on the value line', () => {
  const r = readProfileVersion('profile_version: 1   \n');
  assert.equal(r.status, 'ok');
});

test('a nested profile_version under another key is NOT counted as a hit', () => {
  const r = readProfileVersion('profile_version: 1\nother:\n  profile_version: 2\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

// ---- shapes that must halt ----------------------------------------------------------------------

test('a flow mapping is not a top-level block mapping', () => {
  const r = readProfileVersion('{ profile_version: 1 }\n');
  assert.equal(r.status, 'malformed');
});

test('a quoted key at column 0 is rejected (escape-decoding is out of scope)', () => {
  const r = readProfileVersion('"profile_version": 1\n');
  assert.equal(r.status, 'malformed');
});

test('a tab-indented key is not a top-level key', () => {
  const r = readProfileVersion('\tprofile_version: 1\n');
  assert.equal(r.status, 'malformed');
});

test('an anchor value is rejected — quoted-value rule intentionally does not special-case it', () => {
  const r = readProfileVersion('profile_version: &v 1\n');
  assert.equal(r.status, 'malformed');
});

test('a !!int tag shorthand is rejected', () => {
  const r = readProfileVersion('profile_version: !!int 1\n');
  assert.equal(r.status, 'malformed');
});

test('no space before the hash makes the whole value the string "1#x"', () => {
  const r = readProfileVersion('profile_version: 1#x\n');
  assert.equal(r.status, 'malformed');
});

test('an empty value halts', () => {
  const r = readProfileVersion('profile_version:\n');
  assert.equal(r.status, 'malformed');
});

test('profile_version present but not the FIRST top-level key', () => {
  const r = readProfileVersion('language:\n  code: de\nprofile_version: 1\n');
  assert.equal(r.status, 'missing');
});

test('a %YAML directive halts (leading directive, not a bare document)', () => {
  const r = readProfileVersion('%YAML 1.1\n---\nprofile_version: 1\n');
  assert.equal(r.status, 'malformed');
});

test('a sequence at top level is not a block mapping', () => {
  const r = readProfileVersion('- profile_version: 1\n');
  assert.equal(r.status, 'malformed');
});

test('an explicit key indicator ("? k") halts', () => {
  const r = readProfileVersion('? profile_version\n: 1\n');
  assert.equal(r.status, 'malformed');
});

test('a leading document marker ("---") halts', () => {
  const r = readProfileVersion('---\nprofile_version: 1\n');
  assert.equal(r.status, 'malformed');
});

test('a quoted value halts — the plan overrides tolerating quotes (see references doc)', () => {
  const r = readProfileVersion('profile_version: "1"\n');
  assert.equal(r.status, 'malformed');
  assert.match(r.message, /unquoted integer/);
});

test('no space between the key and colon-adjacent value ("profile_version:1") halts', () => {
  // Real YAML reads this whole line as the scalar "profile_version:1" — a document with NO
  // profile_version key at all. The step-4 shape allowlist halts it as an unrecognized top-level
  // line rather than reporting "missing"; both are halting statuses from the skill's perspective.
  const r = readProfileVersion('profile_version:1\n');
  assert.equal(r.status, 'malformed');
});

// ---- unsupported version --------------------------------------------------------------------

test('an unsupported (but well-formed) version halts with an actionable message', () => {
  const r = readProfileVersion('profile_version: 9\n');
  assert.equal(r.status, 'unsupported');
  assert.equal(r.version, 9);
  assert.match(r.message, /Supported: 1/);
  assert.match(r.message, /migration/);
});

// ---- duplicate key -------------------------------------------------------------------------------

test('duplicate profile_version keys with DIFFERENT values — duplicate, message names both lines', () => {
  const r = readProfileVersion('profile_version: 1\nprofile_version: 2\n');
  assert.equal(r.status, 'duplicate');
  assert.match(r.message, /line 1/);
  assert.match(r.message, /line 2/);
});

test('duplicate profile_version keys with the SAME value still fires (never fail-open on equality)', () => {
  const r = readProfileVersion('profile_version: 1\nprofile_version: 1\n');
  assert.equal(r.status, 'duplicate');
});

// ---- never throws ---------------------------------------------------------------------------

test('non-string input never throws — malformed for null/number/object/array/undefined', () => {
  for (const bad of [null, undefined, 42, {}, [], true]) {
    const r = readProfileVersion(bad);
    assert.equal(r.status, 'malformed');
    assert.equal(r.version, null);
    assert.equal(typeof r.message, 'string');
  }
});

// ---- edge cases (proof-harness-verified against Ruby/Psych, 0 violations) -----------------------

test('double BOM at offset 0 — only the first is stripped, the second is an ordinary character', () => {
  const r = readProfileVersion(BOM + BOM + 'profile_version: 1\n');
  assert.equal(r.status, 'malformed');
});

test('BOM followed later by a lone CR', () => {
  const r = readProfileVersion(BOM + 'profile_version: 1\n# x\rprofile_version: 2\n');
  assert.equal(r.status, 'malformed');
});

test('no trailing newline at EOF still scans ok', () => {
  const r = readProfileVersion('profile_version: 1');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('no trailing newline at EOF with a trailing comment still scans ok', () => {
  const r = readProfileVersion('profile_version: 1 # c');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('a surrogate pair (astral character) in the value halts', () => {
  const r = readProfileVersion('profile_version: 1' + ASTRAL + '\n');
  assert.equal(r.status, 'malformed');
});

test('an astral character used as a key halts (unrelated line, but the shape allowlist still applies)', () => {
  const r = readProfileVersion(ASTRAL + ': 1\nprofile_version: 1\n');
  assert.equal(r.status, 'malformed');
});

test('a very long (400-digit) value parses as an integer and is reported unsupported', () => {
  const r = readProfileVersion('profile_version: ' + '9'.repeat(400) + '\n');
  assert.equal(r.status, 'unsupported');
  assert.equal(typeof r.version, 'number');
});

test('a huge integer beyond Number.MAX_SAFE_INTEGER is still reported unsupported, not a crash', () => {
  const r = readProfileVersion('profile_version: 100000000000000000000\n');
  assert.equal(r.status, 'unsupported');
});

test('a leading-zero value ("01") is tolerated — agrees with a real YAML parser reading Integer 1', () => {
  const r = readProfileVersion('profile_version: 01\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('a tab between the key and the colon is tolerated', () => {
  const r = readProfileVersion('profile_version\t: 1\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('a tab after the colon (before the value) is tolerated', () => {
  const r = readProfileVersion('profile_version:\t1\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('CRLF normalization then a lone CR later halts fail-closed (the safe direction)', () => {
  const r = readProfileVersion('profile_version: 1\r\n# x\rfoo: 2\r\n');
  assert.equal(r.status, 'malformed');
});

// ---- optional CLI (subprocess, exit-code contract) -----------------------------------------------

function runCli(args) {
  return spawnSync(process.execPath, [LIB_PATH, ...args], { encoding: 'utf8' });
}

test('CLI: a valid profile file exits 0 and prints the verdict to stdout', () => {
  const dir = mkdtempSync(join(tmpdir(), 'profile-version-test-'));
  const file = join(dir, 'profile.yml');
  try {
    writeFileSync(file, 'profile_version: 1\n');
    const result = runCli([file]);
    assert.equal(result.status, 0);
    assert.match(result.stdout, /ok/);
    assert.equal(result.stderr, '');
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('CLI: an unsupported profile_version exits 1 with the verdict on stderr', () => {
  const dir = mkdtempSync(join(tmpdir(), 'profile-version-test-'));
  const file = join(dir, 'profile.yml');
  try {
    writeFileSync(file, 'profile_version: 9\n');
    const result = runCli([file]);
    assert.equal(result.status, 1);
    assert.match(result.stderr, /unsupported/);
    assert.equal(result.stdout, '');
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('CLI: a missing path argument exits 2 with a usage message, no stack trace', () => {
  const result = runCli([]);
  assert.equal(result.status, 2);
  assert.match(result.stderr, /usage/);
  assert.doesNotMatch(result.stderr, /\bat file:\/\//);
});

test('CLI: a nonexistent path exits 2 with a clean message, no stack trace', () => {
  const result = runCli(['/nonexistent/path/does-not-exist/profile.yml']);
  assert.equal(result.status, 2);
  assert.doesNotMatch(result.stderr, /\bat file:\/\//);
  assert.doesNotMatch(result.stderr, /Error:\s*ENOENT/); // caught and re-worded, not a raw Node error dump
});

// ---- cross-line structural guard (issue #110): scanStructure ------------------------------------
// readProfileVersion's column-0 scan never inspected what happens BETWEEN top-level keys. The cases
// below are the curated fixtures from the plan's ground-truth table (Psych 3.1.0-verified, ids kept
// for traceability): an unterminated flow collection or quoted scalar, and an alias to an anchor that
// does not exist anywhere in the doc. All three now halt with `status: 'malformed'` and a message
// prefixed `profile_version scan: structural: `. Mechanism B (invalid dedent) is cut this release —
// see references/profile-validation.md — so block scalars stay purely opaque (never flagged), which
// several must-stay-ok cases below exercise directly.

// ---- must-halt (structural) — each proven RED against the pre-#110 lib (scanned 'ok') ------------

test('A1: an unterminated flow collection under a nested key halts', () => {
  const r = readProfileVersion('profile_version: 1\nstack:\n  route_globs: ["a", "b"\n');
  assert.equal(r.status, 'malformed');
  assert.match(r.message, /structural:/);
  assert.match(r.message, /unterminated flow collection/);
});

test('A3: an unterminated double-quoted scalar halts', () => {
  const r = readProfileVersion('profile_version: 1\nname: "unterminated\n');
  assert.equal(r.status, 'malformed');
  assert.match(r.message, /structural:/);
  assert.match(r.message, /unterminated quoted scalar/);
});

test('A4: an unterminated single-quoted scalar halts', () => {
  const r = readProfileVersion('profile_version: 1\nsep: \'a\n');
  assert.equal(r.status, 'malformed');
  assert.match(r.message, /structural:/);
  assert.match(r.message, /unterminated quoted scalar/);
});

test('Q6 (empty-then-flow): an empty-value key makes the next indented line a fresh node, so its unterminated flow halts', () => {
  const r = readProfileVersion('profile_version: 1\nstack:\n  route_globs: ["a"\n');
  assert.equal(r.status, 'malformed');
  assert.match(r.message, /structural:/);
  assert.match(r.message, /unterminated flow collection/);
});

test('Q7 (plain-then-flow): a closed plain value ends the node, so the next key\'s unterminated flow halts', () => {
  const r = readProfileVersion('profile_version: 1\na: text\nb: [unterm\n');
  assert.equal(r.status, 'malformed');
  assert.match(r.message, /structural:/);
  assert.match(r.message, /unterminated flow collection/);
});

test('C2: an inline node-start alias with zero anchors in the doc halts', () => {
  const r = readProfileVersion('profile_version: 1\nglob: *.md\n');
  assert.equal(r.status, 'malformed');
  assert.match(r.message, /structural:/);
  assert.match(r.message, /alias to undefined anchor/);
});

test('C4: an inline node-start alias named "undefined" still halts (no special-casing the name)', () => {
  const r = readProfileVersion('profile_version: 1\nextra: *undefined\n');
  assert.equal(r.status, 'malformed');
  assert.match(r.message, /structural:/);
  assert.match(r.message, /alias to undefined anchor/);
});

test('C9: an alias with a space before it (still node-start after skipping ws) halts', () => {
  const r = readProfileVersion('profile_version: 1\nnote: * foo\n');
  assert.equal(r.status, 'malformed');
  assert.match(r.message, /structural:/);
  assert.match(r.message, /alias to undefined anchor/);
});

test('P11: profile_version itself has no "&" anywhere in the doc, so any later alias halts (BadAlias)', () => {
  const r = readProfileVersion('profile_version: 1\nextra: *whatever\n');
  assert.equal(r.status, 'malformed');
  assert.match(r.message, /structural:/);
  assert.match(r.message, /alias to undefined anchor/);
});

// ---- must-stay-ok (structural) — quote/flow/alias look-alikes that are NOT structural errors ------

test('A5: a quote mid-scalar (not at node-start) is literal text, not an opener', () => {
  const r = readProfileVersion('profile_version: 1\nnote: 5" pipe\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('A6: a quoted phrase inside an unquoted plain scalar is literal text', () => {
  const r = readProfileVersion('profile_version: 1\nnote: he said "hi"\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('A8: a flow collection that wraps to the next line and closes there stays ok', () => {
  const r = readProfileVersion('profile_version: 1\na: [1,\n2]\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('A9: a double-quoted scalar that wraps to the next line and closes there stays ok', () => {
  const r = readProfileVersion('profile_version: 1\na: "l1\n  l2"\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('A10: a closing bracket mid-scalar (not an opener earlier on the line) is literal text', () => {
  const r = readProfileVersion('profile_version: 1\nnote: this is fine]\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('C7: an asterisk mid-plain-scalar is literal text, not an alias', () => {
  const r = readProfileVersion('profile_version: 1\nnote: a*b\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('P12: any "&" anywhere in the doc gates off the undefined-alias check, even for an unrelated alias', () => {
  const r = readProfileVersion('profile_version: 1\na: &x 1\nb: *x\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

// -- compact-colon flow maps (workflow finding 1): a JSON-style value quote opens without a space,
// -- and an untyped/floored flow depth means a bracket-shaped character inside it never mismatch-flags

test('F1: a compact flow map value containing "]" inside its own quotes stays ok', () => {
  const r = readProfileVersion('profile_version: 1\nnote: {"x":"see ] below"}\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('F2: a compact flow map whose value IS the string "]" stays ok', () => {
  const r = readProfileVersion('profile_version: 1\na: {"k":"]"}\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('F3: a compact flow map with single-quoted key/value, value containing "}", stays ok', () => {
  const r = readProfileVersion('profile_version: 1\na: {\'k\':\'}\'}\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('F4: a nested flow sequence-of-map with a "]" inside the map value stays ok', () => {
  const r = readProfileVersion('profile_version: 1\na: [{"k":"]"}]\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('F5: an ordinary compact flow map with no special characters stays ok (control)', () => {
  const r = readProfileVersion('profile_version: 1\na: {"k":"v"}\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

// -- seq-entry plain-scalar fold (workflow finding 2): a "- value" entry opens a plain scalar at the
// -- DASH column, so a more-indented continuation line is a folded continuation, never a fresh node

test('G1: a folded continuation that looks like an unterminated flow opener stays ok', () => {
  const r = readProfileVersion('profile_version: 1\nnote:\n  - value\n    - [x\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('G2: a folded continuation that looks like an unterminated quote opener stays ok', () => {
  const r = readProfileVersion('profile_version: 1\nnote:\n  - value\n    - "x\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('G3: a folded continuation that looks like an undefined alias stays ok', () => {
  const r = readProfileVersion('profile_version: 1\nnote:\n  - value\n    - *x\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('G4: a bare (no-dash) folded continuation under a sequence entry stays ok', () => {
  const r = readProfileVersion('profile_version: 1\nlist:\n  - item\n    [oops\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('G5: a single-quoted folded continuation under a sequence entry stays ok', () => {
  const r = readProfileVersion('profile_version: 1\nlist:\n  - item\n    - \'x\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

// -- comment lines carrying a colon plus an unbalanced/alias look-alike (workflow finding 3): the
// -- whole line is a comment, classified BEFORE any separator/node-start detection

test('H1: a column-0 comment with a colon and an unterminated-looking flow stays ok', () => {
  const r = readProfileVersion('profile_version: 1\n# see: [a, b, c\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('H2: a column-0 comment with a colon and an unterminated-looking quote stays ok', () => {
  const r = readProfileVersion('profile_version: 1\n# fixme: "q\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('H3: a column-0 comment with a colon and an alias look-alike stays ok', () => {
  const r = readProfileVersion('profile_version: 1\n# note: *x\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('H4: an indented comment with a colon and an unterminated-looking flow stays ok', () => {
  const r = readProfileVersion('profile_version: 1\nfoo:\n    # see: [a, b, c\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('H5: the shipped "# internal: []" comment weaponized by a one-char edit ("# internal: [AuthUser") stays ok', () => {
  const r = readProfileVersion('profile_version: 1\nfoo:\n  # internal: [AuthUser\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

// -- a comment starting with NO space right after a flow closer (post-#116 review round): once the
// -- outermost flow node closes, `lexFlowChars` must stop lexing THIS line entirely — a `#` glued
// -- directly to the closing `]`/`}` is still a comment start even though it isn't preceded by
// -- whitespace, and its contents (however bracket-shaped) must never be treated as live flow again.
// -- Each was RED (falsely "unterminated flow collection") before that fix.

test('I1: an empty flow closes, then a zero-space "#[" comment tail stays ok', () => {
  const r = readProfileVersion('profile_version: 1\nnote: []#[\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('I2: a one-element flow closes, then a zero-space "#[" comment tail stays ok', () => {
  const r = readProfileVersion('profile_version: 1\nnote: [1]#[\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('I3: a nested flow closes back to depth 0, then a zero-space "#[" comment tail stays ok', () => {
  const r = readProfileVersion('profile_version: 1\nnote: [[1]]#[\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('I4: a compact flow map closes, then a zero-space "#{" comment tail stays ok', () => {
  const r = readProfileVersion('profile_version: 1\nnote: {a: 1}#{\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('I5: the shipped route_globs shape, closed flow then a zero-space comment naming an open bracket, stays ok', () => {
  const r = readProfileVersion('profile_version: 1\nroute_globs: ["a.php"]#TODO: see issue [42\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('I6 (control): a flow closes with a SPACE before "#[" — already ok pre-fix, must stay ok', () => {
  const r = readProfileVersion('profile_version: 1\nnote: [1] #[\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('I7 (control): a flow closes then a balanced trailing comment — already ok pre-fix, must stay ok', () => {
  const r = readProfileVersion('profile_version: 1\nnote: [1]# balanced comment\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('I8: a genuinely unterminated flow (no closer at all) is still caught after the depth-0 early return', () => {
  const r = readProfileVersion('profile_version: 1\nroute_globs: ["a", "b"\n');
  assert.equal(r.status, 'malformed');
  assert.match(r.message, /unterminated flow collection/);
});

// -- block scalars stay purely opaque (mechanism B is cut this release — never flagged) ------------

test('block scalar: a mid-dedent content line (still deeper than the introducer column) stays ok', () => {
  const r = readProfileVersion('profile_version: 1\nnote: |\n      line one\n   x: [unterm\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('block scalar: a "#"-first content line is literal, not a comment, and stays ok', () => {
  const r = readProfileVersion('profile_version: 1\nnote: |\n  # not a comment\n  real content\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('block scalar: a direct "- |2" sequence-entry block header (innermost dash column) stays ok', () => {
  const r = readProfileVersion('profile_version: 1\nitems:\n  - |2\n      key: [\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('block scalar: a nested "- - |" block header (innermost dash column) stays ok', () => {
  const r = readProfileVersion('profile_version: 1\nm:\n  - - |\n      key: [\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('block scalar: modifier order "|-2" is recognized as a header, body stays opaque', () => {
  const r = readProfileVersion('profile_version: 1\na: |-2\n  k: [\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('block scalar: modifier order "|2-" is recognized as a header, body stays opaque', () => {
  const r = readProfileVersion('profile_version: 1\na: |2-\n  k: [\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('block scalar: header-junk ("| pipe") fails the block-header shape and falls through to plain, never flagged', () => {
  const r = readProfileVersion('profile_version: 1\nnote: | pipe\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('the shipped capture.command block scalar (colons, quotes, backslashes, parens in its body) stays ok', () => {
  const r = readProfileVersion(
    'profile_version: 1\ncommand: |                        # exact, copy-pasteable; engine-specific.\n' +
      '  docker compose run --rm \\\n' +
      '    --user "$(id -u):$(id -g)" \\\n' +
      '    -e HOME=/tmp \\\n' +
      '    --add-host=app.test:host-gateway \\\n' +
      '    app npx playwright test tests/handbook --reporter=line\n',
  );
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

// -- explicit controls named in the plan's ground-truth table ---------------------------------------

test('A7 (control): a properly-closed quoted date format value stays ok', () => {
  const r = readProfileVersion('profile_version: 1\ndate_format: "DD.MM.YYYY"\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('A11 (control): a properly-closed quoted value containing a non-ASCII currency symbol stays ok', () => {
  const r = readProfileVersion('profile_version: 1\ncurrency_symbol: "' + String.fromCodePoint(0x20ac) + '"\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('B9 (control): a sequence of properly-closed quoted scalars stays ok', () => {
  const r = readProfileVersion('profile_version: 1\nroute_globs:\n  - "a"\n  - "b"\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('N1 (control): a plain scalar value containing "://" is not mistaken for a mapping separator', () => {
  const r = readProfileVersion('profile_version: 1\nurl: http://x\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

test('N3 (control): a duplicate key nested under a non-profile_version key is not flagged (only the top-level profile_version key is checked for duplicates)', () => {
  const r = readProfileVersion('profile_version: 1\nother:\n  a: 1\n  a: 2\n');
  assert.equal(r.status, 'ok');
  assert.equal(r.version, 1);
});

// ---- scanStructure called directly (not hidden behind Step-4/tab preemption) ----------------------

test('scanStructure directly: unterminated flow collection at EOF', () => {
  const lines = 'profile_version: 1\na: [1, 2'.split('\n');
  const r = scanStructure(lines);
  assert.ok(r, 'expected a non-null structural verdict');
  assert.equal(r.status, 'malformed');
  assert.equal(r.version, null);
  assert.equal(r.message, 'profile_version scan: structural: unterminated flow collection');
});

test('scanStructure directly: unterminated quoted scalar at EOF', () => {
  const lines = 'profile_version: 1\nname: "abc'.split('\n');
  const r = scanStructure(lines);
  assert.ok(r, 'expected a non-null structural verdict');
  assert.equal(r.status, 'malformed');
  assert.equal(r.version, null);
  assert.equal(r.message, 'profile_version scan: structural: unterminated quoted scalar');
});

test('scanStructure directly: alias to an undefined anchor (zero "&" in the doc)', () => {
  const lines = 'profile_version: 1\nextra: *nope'.split('\n');
  const r = scanStructure(lines);
  assert.ok(r, 'expected a non-null structural verdict');
  assert.equal(r.status, 'malformed');
  assert.equal(r.version, null);
  assert.equal(r.message, 'profile_version scan: structural: alias to undefined anchor');
});

test('scanStructure directly: a quote not at node-start is literal text, returns null', () => {
  const lines = 'profile_version: 1\nnote: 5" pipe'.split('\n');
  assert.equal(scanStructure(lines), null);
});

test('scanStructure directly: "*" mid-plain-scalar is literal, not an alias, returns null', () => {
  const lines = 'profile_version: 1\nnote: a*b'.split('\n');
  assert.equal(scanStructure(lines), null);
});

test('scanStructure directly: any "&" anywhere in the doc gates off the undefined-alias check, returns null', () => {
  const lines = 'profile_version: 1\na: &x 1\nb: *x'.split('\n');
  assert.equal(scanStructure(lines), null);
});

// ---- precedence: missing → duplicate → malformed, structural sits in the malformed tier -----------

test('precedence: a duplicate profile_version key wins over a later structural error (status stays "duplicate")', () => {
  const r = readProfileVersion('profile_version: 1\nprofile_version: 2\nextra: [unterm\n');
  assert.equal(r.status, 'duplicate');
});

test('precedence: profile_version not being the first key wins over a later structural error (status stays "missing")', () => {
  const r = readProfileVersion('language:\n  code: de\nprofile_version: 1\nextra: [unterm\n');
  assert.equal(r.status, 'missing');
});

test('precedence: a valid, first-key profile_version with a real structural error surfaces as "malformed"', () => {
  const r = readProfileVersion('profile_version: 1\nextra: [unterm\n');
  assert.equal(r.status, 'malformed');
  assert.match(r.message, /structural:/);
});

// ---- never throws (structural adversarial inputs) --------------------------------------------------

for (const [label, raw] of [
  [
    'a long "- - - … x" nested-sequence chain (iterative design — no recursion overflow)',
    'profile_version: 1\nlist:\n  ' + '- '.repeat(5000) + 'x\n',
  ],
  [
    'deeply nested flow-collection brackets',
    'profile_version: 1\na: ' + '['.repeat(5000) + ']'.repeat(5000) + '\n',
  ],
  ['a huge single plain-scalar line', 'profile_version: 1\nnote: ' + 'x'.repeat(200000) + '\n'],
  ['a lone "-" sequence entry with an empty node (next line is its node)', 'profile_version: 1\nlist:\n  -\n'],
]) {
  test(`structural scan never throws: ${label}`, () => {
    const r = readProfileVersion(raw);
    assert.equal(typeof r, 'object');
    assert.equal(typeof r.status, 'string');
    assert.equal(typeof r.message, 'string');
  });
}
