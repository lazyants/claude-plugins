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
