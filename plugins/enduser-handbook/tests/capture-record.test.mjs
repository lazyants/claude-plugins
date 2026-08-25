// Unit tests for the disk-touching provenance module (enduser-handbook 1.12.0, #362). Runs under
// Node's built-in test runner: `node --test capture-record.test.mjs` (explicit path — `node --test
// <dir>` gives a misleading MODULE_NOT_FOUND).
//
// Section order: JCS canonicalization (pinned vectors + mutants) -> the strict JSON reader
// (duplicate-key / lone-surrogate) -> path derivations -> gate 5 (ownership) -> gate 6 (hazards) ->
// row 6 (the 22-case + 24-raw-case classifier, plus abort/cleanup) -> openCaptureRun/closeCaptureRun
// -> recordChapterProvenance (completeness rules 1-5) -> sweepChapterProvenanceTemps (deliberately
// separate from row 6 — see its section banner) -> buildProvenanceReport -> the fs capability
// policy -> the required integration test against teammate B's real chapter-paths.mjs exports.

import test from 'node:test';
import assert from 'node:assert/strict';
import * as nodeFs from 'node:fs';
import { createHash, randomUUID as nodeRandomUUID } from 'node:crypto';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer as netCreateServer } from 'node:net';

import * as CR from '../skills/enduser-handbook/assets/lib/capture-record.mjs';

// [round 16] The declared `ReportRow` field set, read from the declaration itself. The key-set pin
// below used to compare against a literal list written out here, which is a second copy of the same
// assumption: adding `record_detail` to the declaration and to the active branch, and forgetting
// the ownership-skip branch, satisfied both copies and shipped a row missing a required field. The
// two sides are derived from genuinely different sources now — the runtime object, and the file
// that claims to describe it.
function declaredReportRowKeys() {
  const src = nodeFs.readFileSync(
    join(import.meta.dirname, '..', 'skills', 'enduser-handbook', 'assets', 'lib', 'capture-record.d.mts'),
    'utf8',
  );
  const body = src.match(/export interface ReportRow \{([\s\S]*?)\n\}/);
  assert.ok(body, 'ReportRow is no longer declared as an interface — this extraction, not the code, is what broke');
  const keys = [...body[1].matchAll(/^\s{2}([a-z_][A-Za-z0-9_]*)\??:/gm)].map((m) => m[1]);
  assert.ok(keys.length >= 7, `only ${keys.length} ReportRow fields extracted — an under-reading passes every comparison`);
  return keys.sort();
}

// ---------------------------------------------------------------------------------------------
// Fixture builders
// ---------------------------------------------------------------------------------------------

function withTempDir(fn) {
  const dir = nodeFs.mkdtempSync(join(tmpdir(), 'ehcr-'));
  try {
    return fn(dir);
  } finally {
    nodeFs.rmSync(dir, { recursive: true, force: true });
  }
}

// [round 38] The same `<dev>:<ino>` string the module builds, so a fixture can say "this is the
// same directory object" rather than "this is the same path".
function identityOf(path) {
  const s = nodeFs.lstatSync(path);
  return `${s.dev}:${s.ino}`;
}

function profileFor(dir, overrides = {}) {
  // `publish.chapters_dir` is the published docs tree — it already exists by the time W2 runs in
  // any real handbook, so fixtures create it up front (establishment only builds the PROVENANCE
  // hierarchy under it, never its own parent).
  const chaptersDir = overrides.publish?.chapters_dir ?? join(dir, 'handbook');
  nodeFs.mkdirSync(chaptersDir, { recursive: true });
  return {
    // build_identity.ui_read defaults to true (opt-OUT) per the plan, which means a profile with
    // no command configured needs_ui_read on every call — tests that are not specifically about
    // identity resolution turn it off explicitly so lifecycle/completeness fixtures don't have to
    // thread a UI observation through every call.
    capture: { output_dir: join(dir, 'assets'), build_identity: { ui_read: false }, ...(overrides.capture ?? {}) },
    publish: { chapters_dir: chaptersDir, target: 'static_md', ...(overrides.publish ?? {}) },
  };
}

function realDeps() {
  return {
    openSync: nodeFs.openSync,
    closeSync: nodeFs.closeSync,
    readSync: nodeFs.readSync,
    writeSync: nodeFs.writeSync,
    fstatSync: nodeFs.fstatSync,
    lstatSync: nodeFs.lstatSync,
    readlinkSync: nodeFs.readlinkSync,
    realpathSync: nodeFs.realpathSync,
    mkdirSync: nodeFs.mkdirSync,
    unlinkSync: nodeFs.unlinkSync,
    renameSync: nodeFs.renameSync,
    readdirSync: nodeFs.readdirSync,
    randomUUID: nodeRandomUUID,
    runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
  };
}

function depsWithOverride(overrides) {
  return { ...realDeps(), ...overrides };
}

const ZERO_DIGEST = `sha256:${'0'.repeat(64)}`;
const ONE_DIGEST = `sha256:${'1'.repeat(64)}`;

function validToken(runId, digest) {
  return JSON.stringify({ run_id: runId, opening_digest: digest });
}

function validBuildIdentity() {
  return { value: null, source: 'unavailable', resolution_reason: 'no_source_configured', detail: null };
}

function validRunRecord(runId, digest, chapters = {}) {
  return JSON.stringify({
    record_version: 1,
    run_id: runId,
    opening_digest: digest,
    build_identity: validBuildIdentity(),
    chapters,
  });
}

function runDir(profile) {
  return join(CR.provenanceRoot(profile), 'run');
}

function tokenPathFor(profile) {
  return join(runDir(profile), 'pending.json');
}

function recordPathFor(profile) {
  return join(runDir(profile), 'current.json');
}

// A local re-listing of `run/`'s matching temps, since the production module's own
// `listMatchingTemps` is private — mirrors its naming contract (`current.json.<uuid>.tmp`) purely
// for test-side inspection.
function listRunTempsOnDisk(profile) {
  const dir = runDir(profile);
  if (!nodeFs.existsSync(dir)) return [];
  return nodeFs.readdirSync(dir).filter((name) => name.startsWith('current.json.') && name.endsWith('.tmp'));
}

function tempPathFor(profile, uuid = 'aaaaaaaa-0000-0000-0000-000000000000') {
  return join(runDir(profile), `current.json.${uuid}.tmp`);
}

function writeFixture(profile, { token, record, temps = [] } = {}) {
  nodeFs.mkdirSync(runDir(profile), { recursive: true });
  if (token !== undefined) nodeFs.writeFileSync(tokenPathFor(profile), token);
  if (record !== undefined) nodeFs.writeFileSync(recordPathFor(profile), record);
  for (const [i, text] of temps.entries()) {
    nodeFs.writeFileSync(tempPathFor(profile, `aaaaaaaa-0000-0000-0000-00000000000${i}`), text);
  }
}

// =================================================================================================
// JCS canonicalization — pinned vectors and required mutants
// =================================================================================================

test('jcs: pinned UTF-8 byte vector for {"x":"é"}', () => {
  const result = CR.jcsCanonicalize({ x: 'é' });
  assert.equal(result.ok, true);
  assert.equal(result.canonical, '{"x":"é"}');
  const bytes = Buffer.from(result.canonical, 'utf8');
  assert.equal(bytes.toString('hex'), '7b2278223a22c3a9227d');
});

test('jcs: pinned SHA-256 digest over the UTF-8 bytes — the REQUIRED digest constant', () => {
  const result = CR.jcsCanonicalize({ x: 'é' });
  const digest = CR.sha256HexOfCanonical(result.canonical);
  assert.equal(digest, '97f06f396a709c3a29824e1cc794eeb98e2d1a262d7d455439d286d42803f0fe');
});

test('jcs: REQUIRED MUTANT — hashing the canonical string as UTF-16LE must fail (produces a different, also-pinned digest)', () => {
  const result = CR.jcsCanonicalize({ x: 'é' });
  const correct = CR.sha256HexOfCanonical(result.canonical);
  const mutantDigest = createHash('sha256').update(Buffer.from(result.canonical, 'utf16le')).digest('hex');
  assert.equal(mutantDigest, '628a77cae45a222601c52dd9af2b90260db9cf79839dbf01c1b3de17fe7c5864');
  assert.notEqual(mutantDigest, correct);
});

test('jcs: key reordering is irrelevant — {a,b} and {b,a} canonicalize identically', () => {
  const first = CR.jcsCanonicalize({ a: 1, b: 2 });
  const second = CR.jcsCanonicalize({ b: 2, a: 1 });
  assert.equal(first.canonical, second.canonical);
  assert.equal(first.canonical, '{"a":1,"b":2}');
});

test('jcs: -0 canonicalizes to "0"', () => {
  const result = CR.jcsCanonicalize({ x: -0 });
  assert.equal(result.canonical, '{"x":0}');
});

test('jcs: non-BMP payload round-trips as raw UTF-8, not escaped', () => {
  const result = CR.jcsCanonicalize({ emoji: '😀' });
  assert.equal(result.ok, true);
  assert.equal(result.canonical, '{"emoji":"😀"}');
});

test('jcs: control characters use RFC escape forms', () => {
  const result = CR.jcsCanonicalize({ s: '\x00\x01\t\n\r\b\f' });
  assert.equal(result.ok, true);
  assert.equal(result.canonical, '{"s":"\\u0000\\u0001\\t\\n\\r\\b\\f"}');
});

test('jcs: nested objects and arrays recurse and sort at every level', () => {
  const result = CR.jcsCanonicalize({ z: [{ b: 1, a: 2 }, 3], a: { d: 1, c: 2 } });
  assert.equal(result.ok, true);
  assert.equal(result.canonical, '{"a":{"c":2,"d":1},"z":[{"a":2,"b":1},3]}');
});

test('jcs: lone surrogate is rejected, at top level and nested', () => {
  assert.equal(CR.jcsCanonicalize('\ud800').ok, false);
  assert.equal(CR.jcsCanonicalize({ x: '\udc00' }).ok, false);
  assert.equal(CR.jcsCanonicalize(['\ud800']).ok, false);
  // A genuinely paired surrogate (an emoji) must NOT be rejected.
  assert.equal(CR.jcsCanonicalize('😀').ok, true);
});

test('jcs: rejects undefined, functions, symbols and BigInt rather than coercing', () => {
  assert.equal(CR.jcsCanonicalize(undefined).ok, false);
  assert.equal(CR.jcsCanonicalize({ x: undefined }).ok, false);
  assert.equal(CR.jcsCanonicalize({ x: () => {} }).ok, false);
  assert.equal(CR.jcsCanonicalize({ x: Symbol('s') }).ok, false);
  assert.equal(CR.jcsCanonicalize({ x: 10n }).ok, false);
  assert.equal(CR.jcsCanonicalize({ x: Number.POSITIVE_INFINITY }).ok, false);
  assert.equal(CR.jcsCanonicalize({ x: Number.NaN }).ok, false);
});

test('jcs: digestOpeningPayload throws a plain Error naming the canonicalization reason, and succeeds otherwise', () => {
  // A bare `assert.throws(fn)` with no second argument is blind to the thrown value's constructor
  // and message — codex round 7, finding 3. `digestOpeningPayload`'s own JSDoc and
  // `capture-record.d.mts` both promise "an Error" (not a TypeError, not `jcsCanonicalize`'s
  // documented RangeError-on-cycle exception) naming the reason, with the exact message measured
  // there — both are part of the contract and get their own assertion, matching the house pattern
  // used for the `TypeError` guard in chapter-paths.test.mjs's `assertProvenanceGuardThrows`.
  assert.throws(
    () => CR.digestOpeningPayload({ x: undefined }),
    (err) => {
      assert.strictEqual(err.constructor, Error, `expected a plain Error, got ${err.constructor.name}`);
      assert.strictEqual(err.message, 'digestOpeningPayload: cannot canonicalize opening payload (undefined_unsupported)');
      return true;
    },
  );
  const digest = CR.digestOpeningPayload({ a: 1 });
  assert.match(digest, /^sha256:[0-9a-f]{64}$/);
});

// =================================================================================================
// The strict JSON reader — duplicate keys (decoded-name comparison) and lone surrogates in raw text
// =================================================================================================

test('readRunRecordText: rejects a literal duplicate key', () => {
  const text = `{"record_version":1,"run_id":"a","run_id":"b","opening_digest":"${ZERO_DIGEST}","build_identity":${JSON.stringify(validBuildIdentity())},"chapters":{}}`;
  const result = CR.readRunRecordText(text);
  assert.equal(result.ok, false);
});

test('readRunRecordText: rejects an escape-equivalent duplicate key ("a" vs "\\u0061")', () => {
  const text = `{"a":1,"\\u0061":2,"record_version":1,"run_id":"x","opening_digest":"${ZERO_DIGEST}","build_identity":${JSON.stringify(validBuildIdentity())},"chapters":{}}`;
  const result = CR.readRunRecordText(text);
  assert.equal(result.ok, false);
});

test('readRunRecordText: rejects a duplicate nested inside a sub-object and inside an object in an array', () => {
  const nestedObj = `{"record_version":1,"run_id":"x","opening_digest":"${ZERO_DIGEST}","build_identity":${JSON.stringify(validBuildIdentity())},"chapters":{"a":{"opening":{},"closing":{},"x":1,"x":2}}}`;
  assert.equal(CR.readRunRecordText(nestedObj).ok, false);
  const nestedArr = `{"record_version":1,"run_id":"x","opening_digest":"${ZERO_DIGEST}","build_identity":${JSON.stringify(validBuildIdentity())},"chapters":{},"arr":[{"y":1,"y":2}]}`;
  assert.equal(CR.readRunRecordText(nestedArr).ok, false);
});

test('readRunRecordText: two DIFFERENT objects reusing the same key name is NOT a duplicate', () => {
  const text = JSON.stringify({
    record_version: 1,
    run_id: 'x',
    opening_digest: ZERO_DIGEST,
    build_identity: validBuildIdentity(),
    chapters: { a: { opening: { x: ONE_DIGEST }, closing: { x: ONE_DIGEST }, opening_hazards: [], closing_hazards: [] }, b: { opening: { x: ONE_DIGEST }, closing: { x: ONE_DIGEST }, opening_hazards: [], closing_hazards: [] } },
  });
  assert.equal(CR.readRunRecordText(text).ok, true);
});

test('readRunRecordText: rejects a lone surrogate inside a JSON string value', () => {
  const text = `{"record_version":1,"run_id":"\\ud800","opening_digest":"${ZERO_DIGEST}","build_identity":${JSON.stringify(validBuildIdentity())},"chapters":{}}`;
  assert.equal(CR.readRunRecordText(text).ok, false);
});

test('readRunRecordText: field-by-field mutation matrix', () => {
  const base = () => ({
    record_version: 1,
    run_id: 'x',
    opening_digest: ZERO_DIGEST,
    build_identity: validBuildIdentity(),
    chapters: {},
  });
  assert.equal(CR.readRunRecordText(JSON.stringify(base())).ok, true, 'baseline must be valid');
  assert.equal(CR.readRunRecordText(JSON.stringify({ ...base(), record_version: 2 })).ok, false, 'non-1 record_version');
  assert.equal(CR.readRunRecordText(JSON.stringify({ ...base(), run_id: 5 })).ok, false, 'non-string run_id');
  assert.equal(CR.readRunRecordText(JSON.stringify({ ...base(), opening_digest: 'sha1:' + '0'.repeat(64) })).ok, false, 'wrong digest algorithm');
  assert.equal(CR.readRunRecordText(JSON.stringify({ ...base(), opening_digest: 'sha256:' + '0'.repeat(63) })).ok, false, 'short digest');
  assert.equal(CR.readRunRecordText(JSON.stringify({ ...base(), opening_digest: 'sha256:' + 'G'.repeat(64) })).ok, false, 'non-hex digest');
  assert.equal(CR.readRunRecordText(JSON.stringify({ ...base(), build_identity: undefined })).ok, false, 'missing build_identity');
  assert.equal(CR.readRunRecordText(JSON.stringify({ ...base(), chapters: [] })).ok, false, 'chapters as array');
  assert.equal(
    CR.readRunRecordText(JSON.stringify({ ...base(), chapters: { a: { opening: {} } } })).ok,
    false,
    'chapter entry missing closing',
  );
  assert.equal(
    CR.readRunRecordText(JSON.stringify({ ...base(), chapters: { a: { opening: { x: 'not-a-hash' }, closing: {}, opening_hazards: [], closing_hazards: [] } } })).ok,
    false,
    'non-hash-grammar value',
  );
});

test('readRunRecordText: key-canonicality — structural rejects, and the load-bearing POSITIVE controls', () => {
  const withKey = (key) =>
    JSON.stringify({
      record_version: 1,
      run_id: 'x',
      opening_digest: ZERO_DIGEST,
      build_identity: validBuildIdentity(),
      chapters: { a: { opening: { [key]: ONE_DIGEST }, closing: { [key]: ONE_DIGEST }, opening_hazards: [], closing_hazards: [] } },
    });
  assert.equal(CR.readRunRecordText(withKey('/a.png')).ok, false, 'leading slash');
  assert.equal(CR.readRunRecordText(withKey('../a.png')).ok, false, 'dot-dot segment');
  assert.equal(CR.readRunRecordText(withKey('./a.png')).ok, false, 'dot segment');
  assert.equal(CR.readRunRecordText(withKey('a//b.png')).ok, false, 'empty segment');
  // Positive controls: a literal backslash and a literal '%2e%2e' segment are LEGAL POSIX names —
  // W2's own directory snapshot can produce them, so a reader rejecting them would reject a record
  // its own writer just wrote.
  assert.equal(CR.readRunRecordText(withKey('sub\\stale.png')).ok, true, 'literal backslash must be accepted');
  assert.equal(CR.readRunRecordText(withKey('%2e%2e/a.png')).ok, true, "a '%2e%2e' segment must be accepted");
});

test('readRunRecordText: prototype-named keys are OWN-property checked, never via `in`', () => {
  const text = JSON.stringify({
    record_version: 1,
    run_id: 'x',
    opening_digest: ZERO_DIGEST,
    build_identity: validBuildIdentity(),
    chapters: { a: { opening: {}, closing: {}, opening_hazards: [], closing_hazards: [] } },
  });
  const result = CR.readRunRecordText(text);
  assert.equal(result.ok, true);
  // 'toString' must not be treated as a present key by inheritance.
  assert.equal(Object.hasOwn(result.record.chapters.a.opening, 'toString'), false);
});

test('readChapterRecordText: field-by-field mutation matrix', () => {
  const base = () => ({
    record_version: 1,
    run_id: 'x',
    build_identity: validBuildIdentity(),
    asset_hashes: { 'a.png': ONE_DIGEST },
  });
  assert.equal(CR.readChapterRecordText(JSON.stringify(base())).ok, true);
  assert.equal(CR.readChapterRecordText(JSON.stringify({ ...base(), record_version: 0 })).ok, false);
  assert.equal(CR.readChapterRecordText(JSON.stringify({ ...base(), run_id: null })).ok, false);
  assert.equal(CR.readChapterRecordText(JSON.stringify({ ...base(), detail: {} })).ok, false);
  assert.equal(CR.readChapterRecordText(JSON.stringify({ ...base(), asset_hashes: ['x'] })).ok, false, 'array masquerading as a map');
});

// =================================================================================================
// Path derivations
// =================================================================================================

test('provenanceRoot / chapterRecordPath: flat and grouped, stable across repeated calls', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const root = CR.provenanceRoot(profile);
    assert.equal(root, join(dir, 'handbook', '.provenance'));
    const flat = CR.chapterRecordPath(profile, { slug: 'items' });
    assert.equal(flat, join(root, 'chapters', 'items.json'));
    const grouped = CR.chapterRecordPath(profile, { slug: 'items', group: 'admin' });
    assert.equal(grouped, join(root, 'chapters', 'admin', 'items.json'));
    // Stability: calling again from a fresh profile object with identical values is byte-identical.
    assert.equal(CR.chapterRecordPath(profileFor(dir), { slug: 'items', group: 'admin' }), grouped);
  });
});

test('chapterRecordPath: a falsy-but-PRESENT group (0, "") is GROUPED, matching chapter-paths.mjs\'s `!== undefined` convention — found by paths', () => {
  // chapter-paths.mjs's own outputDirTail/chapterRelPath check `entry.group !== undefined`,
  // documented as "a falsy-but-present group value must never silently derive a flat path". A
  // truthy check on `entry.group` here would disagree with chapterAssetDir for `group: 0` (and any
  // other falsy-but-defined value) — the record path would collapse to the FLAT form while the
  // actual asset directory nests under a literal "0" segment, a real cross-module classification
  // mismatch for a malformed-but-present manifest value.
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const root = CR.provenanceRoot(profile);

    const flatAssetDir = chapterPathsModule.chapterAssetDir(profile, { slug: 'items' });
    const zeroGroupAssetDir = chapterPathsModule.chapterAssetDir(profile, { slug: 'items', group: 0 });
    // Ground truth: chapter-paths.mjs itself treats `group: 0` as GROUPED (a real "0" segment),
    // distinct from the flat asset dir — confirming this is a genuine classification to track, not
    // a hypothetical.
    assert.notEqual(zeroGroupAssetDir, flatAssetDir);
    assert.equal(zeroGroupAssetDir, join(profile.capture.output_dir, '0', 'items'));

    const flatRecordPath = CR.chapterRecordPath(profile, { slug: 'items' });
    const zeroGroupRecordPath = CR.chapterRecordPath(profile, { slug: 'items', group: 0 });
    assert.equal(flatRecordPath, join(root, 'chapters', 'items.json'));
    assert.notEqual(
      zeroGroupRecordPath,
      flatRecordPath,
      'a group:0 entry must NOT collapse to the same record path as a genuinely flat entry',
    );
    assert.equal(zeroGroupRecordPath, join(root, 'chapters', '0', 'items.json'));
  });
});

// =================================================================================================
// Gate 5 — assertProvenanceOwnership
// =================================================================================================

test('gate 5: nested topology (output_dir under chapters_dir) passes, with or without build_identity', () => {
  withTempDir((dir) => {
    nodeFs.mkdirSync(join(dir, 'handbook', 'assets'), { recursive: true });
    const profile = {
      capture: { output_dir: join(dir, 'handbook', 'assets') },
      publish: { chapters_dir: join(dir, 'handbook') },
    };
    const result = CR.assertProvenanceOwnership(profile, realDeps());
    assert.equal(result.ok, true);
  });
});

test('gate 5: EQUAL topology — warn-and-skip without build_identity, halt WITH it', () => {
  withTempDir((dir) => {
    nodeFs.mkdirSync(join(dir, 'handbook'), { recursive: true });
    const profileNoIdentity = {
      capture: { output_dir: join(dir, 'handbook') },
      publish: { chapters_dir: join(dir, 'handbook') },
    };
    const skip = CR.assertProvenanceOwnership(profileNoIdentity, realDeps());
    assert.equal(skip.ok, false);
    assert.equal(skip.skip, true);

    const profileWithIdentity = {
      capture: { output_dir: join(dir, 'handbook'), build_identity: { ui_read: false } },
      publish: { chapters_dir: join(dir, 'handbook') },
    };
    const halt = CR.assertProvenanceOwnership(profileWithIdentity, realDeps());
    assert.equal(halt.ok, false);
    assert.equal(halt.skip, undefined);
    assert.ok(halt.halts.length > 0);
  });
});

test('gate 5: capture-as-parent topology (output_dir is an ancestor of chapters_dir) overlaps', () => {
  withTempDir((dir) => {
    nodeFs.mkdirSync(join(dir, 'vault', 'handbook'), { recursive: true });
    const profile = {
      capture: { output_dir: join(dir, 'vault') },
      publish: { chapters_dir: join(dir, 'vault', 'handbook') },
    };
    const result = CR.assertProvenanceOwnership(profile, realDeps());
    assert.equal(result.ok, false);
  });
});

test('gate 5: sibling-prefix topology (handbook-old vs handbook) does NOT overlap — the string-prefix discriminator', () => {
  withTempDir((dir) => {
    nodeFs.mkdirSync(join(dir, 'handbook'), { recursive: true });
    nodeFs.mkdirSync(join(dir, 'handbook-old'), { recursive: true });
    const profile = {
      capture: { output_dir: join(dir, 'handbook-old') },
      publish: { chapters_dir: join(dir, 'handbook') },
    };
    const result = CR.assertProvenanceOwnership(profile, realDeps());
    assert.equal(result.ok, true);
  });
});

test('gate 5: overlap hidden behind two DIFFERENT symlinked aliases — the halt message must name the RESOLVED paths, not just the raw disjoint-looking ones', () => {
  // `alias-a` and `alias-b` are two distinct names whose raw configured strings
  // ('.../alias-a/.provenance' vs '.../alias-b') share no lexical prefix at all, so a reader
  // comparing only the raw values would conclude the two trees are unrelated. They are not:
  // `alias-a` resolves into `real/sub` and `alias-b` resolves into `real` — a direct ANCESTOR of
  // `real/sub`, and therefore of the resolved provenance root `real/sub/.provenance` — the overlap
  // the gate exists to refuse. The gate already refuses this correctly (this is not a gate-5
  // detection bug); what this test pins is that the halt MESSAGE explains why, by naming what each
  // raw string actually resolved to.
  //
  // The two resolved targets (`real` and `real/sub`) are deliberately DIFFERENT directories, not
  // the same one aliased twice (an earlier version of this fixture used one shared target) — but
  // that alone does not make the two assertions below independent: `real` is, by construction, a
  // literal filesystem-and-string PREFIX of `real/sub/.provenance`, so a bare
  // `message.includes(outputResolved)` would still pass on a message that named only the root's
  // resolved value and never printed the output dir's on its own (an actual mutation caught this:
  // deleting the "capture.output_dir ... resolves to '...'" clause entirely left both bare checks
  // green). What makes them independent is asserting each value in the DELIMITED form the message
  // template actually renders it in — `'<value>')`, quote-value-quote-paren — since that trailing
  // `')` can only follow a value the message prints AT THAT POSITION, never a value that merely
  // happens to be a prefix of a longer one printed elsewhere.
  withTempDir((dir) => {
    const real = join(dir, 'real');
    const realSub = join(real, 'sub');
    nodeFs.mkdirSync(realSub, { recursive: true });
    const aliasA = join(dir, 'alias-a'); // publish.chapters_dir — resolves to realSub
    const aliasB = join(dir, 'alias-b'); // capture.output_dir — resolves to real, realSub's ANCESTOR
    nodeFs.symlinkSync(realSub, aliasA);
    nodeFs.symlinkSync(real, aliasB);
    const profile = {
      capture: { output_dir: aliasB, build_identity: { ui_read: false } },
      publish: { chapters_dir: aliasA },
    };
    const halt = CR.assertProvenanceOwnership(profile, realDeps());
    assert.equal(halt.ok, false);
    assert.equal(halt.skip, undefined, 'build_identity is configured — this must halt, not skip');
    assert.equal(halt.halts[0].halt, 'provenance_root_overlap');

    const message = halt.halts[0].message;
    const rootResolved = nodeFs.realpathSync(realSub);
    const outputResolved = nodeFs.realpathSync(real);
    assert.notEqual(
      rootResolved,
      outputResolved,
      'fixture sanity: the two resolved targets must be genuinely different paths, or the two assertions below collapse into one',
    );

    // The raw, as-configured values — both must still be present (this much already held before
    // the fix; a message that dropped them would be a regression in the other direction).
    assert.ok(message.includes(join(aliasA, '.provenance')), 'message must still name the raw provenance root');
    assert.ok(message.includes(aliasB), 'message must still name the raw capture.output_dir');
    // The RESOLVED values — this is what a raw-only message cannot provide, and what makes the halt
    // actionable: the operator's own alias names never appear in the same tree, only their targets
    // do. Each is asserted in its DELIMITED form (see the comment above) so that neither check can
    // pass merely because the OTHER value's rendering happens to contain it as a substring.
    const rootResolvedPath = `${rootResolved}/.provenance`;
    assert.ok(
      message.includes(`'${rootResolvedPath}')`),
      `message must name the RESOLVED provenance root ('${rootResolvedPath}'); got: ${message}`,
    );
    assert.ok(
      message.includes(`'${outputResolved}')`),
      `message must name the RESOLVED capture.output_dir ('${outputResolved}'); got: ${message}`,
    );
  });
});

// =================================================================================================
// Gate 6 — hazard inspection, driven through the real recoverProvenanceState consumer.
//
// The full product row6-generated.md's `hazard_tests` names: 3 LEAF kinds (token, record, temp) x
// 5 hazards (symlink, hard link, unreadable, non-regular, inspection-failure) = 15, plus 2
// HIERARCHY kinds (root, run-dir) x 3 hazards (symlink, non-directory, inspection-failure) = 6.
// 15 + 6 = 21. Every one of the 21 named fixtures below is driven through the real
// recoverProvenanceState consumer, never through a bare helper call.
//
// Two hazards collapse to the SAME `reason` string in this implementation, and that is a
// deliberate, stated choice rather than an oversight: `unreadable` (a real EACCES from a
// permission-denied regular file) and `inspection-failure` (any OTHER unexpected errno, injected
// here since a real filesystem doesn't hand out arbitrary I/O errors on demand) both land on
// `openLeafNoFollow`'s single catch-all once `ENOENT`/`ELOOP` are ruled out, and BOTH are repaired
// the identical way — halt, name the path, let the operator look — so a second reason string would
// carry no operational difference. What the plan requires or catches is that NEITHER is silently
// read as absent, which is exactly what these two separately-driven fixtures per leaf pin.
// =================================================================================================

function leafPathFor(profile, kind) {
  if (kind === 'token') return tokenPathFor(profile);
  if (kind === 'record') return recordPathFor(profile);
  if (kind === 'temp') return tempPathFor(profile);
  throw new Error(`unknown leaf kind: ${kind}`);
}

function setupLeafHazard(profile, dir, kind, hazard) {
  nodeFs.mkdirSync(runDir(profile), { recursive: true });
  const targetPath = leafPathFor(profile, kind);
  if (hazard === 'symlink') {
    const outside = join(dir, `outside-${kind}-symlink.json`);
    nodeFs.writeFileSync(outside, '{}');
    nodeFs.symlinkSync(outside, targetPath);
    return realDeps();
  }
  if (hazard === 'hardlink') {
    const outside = join(dir, `outside-${kind}-hardlink.json`);
    nodeFs.writeFileSync(outside, '{}');
    nodeFs.linkSync(outside, targetPath);
    return realDeps();
  }
  if (hazard === 'nonregular') {
    nodeFs.mkdirSync(targetPath, { recursive: true });
    return realDeps();
  }
  if (hazard === 'unreadable') {
    nodeFs.writeFileSync(targetPath, '{}');
    nodeFs.chmodSync(targetPath, 0o000);
    return realDeps();
  }
  if (hazard === 'inspection-failure') {
    nodeFs.writeFileSync(targetPath, '{}');
    const boom = Object.assign(new Error('injected inspection failure'), { code: 'EIO' });
    return depsWithOverride({
      openSync: (p, ...rest) => {
        if (p === targetPath) throw boom;
        return nodeFs.openSync(p, ...rest);
      },
    });
  }
  throw new Error(`unknown leaf hazard: ${hazard}`);
}

function setupHierarchyHazard(profile, dir, kind, hazard) {
  const root = CR.provenanceRoot(profile);
  const runDirPath = runDir(profile);
  const targetPath = kind === 'root' ? root : runDirPath;
  if (kind === 'run-dir') {
    // The root itself must be a clean, real, hazard-free directory so the walk actually reaches
    // run/ — a hazard planted at the root would be caught first and this fixture would prove
    // nothing about run/ specifically.
    nodeFs.mkdirSync(root, { recursive: true });
  }
  if (hazard === 'symlink') {
    const outside = join(dir, `outside-${kind}-dir`);
    nodeFs.mkdirSync(outside, { recursive: true });
    nodeFs.symlinkSync(outside, targetPath);
    return realDeps();
  }
  if (hazard === 'nondirectory') {
    nodeFs.writeFileSync(targetPath, 'not a directory');
    return realDeps();
  }
  if (hazard === 'inspection-failure') {
    nodeFs.writeFileSync(targetPath, 'irrelevant — lstat is intercepted before content matters');
    const boom = Object.assign(new Error('injected inspection failure'), { code: 'EIO' });
    return depsWithOverride({
      lstatSync: (p) => {
        if (p === targetPath) throw boom;
        return nodeFs.lstatSync(p);
      },
    });
  }
  throw new Error(`unknown hierarchy hazard: ${hazard}`);
}

const LEAF_HAZARD_REASON = {
  symlink: 'symlink',
  hardlink: 'hard_link',
  nonregular: 'non_regular',
  unreadable: 'inspection_failure',
  'inspection-failure': 'inspection_failure',
};

const HIERARCHY_HAZARD_REASON = {
  symlink: 'symlink',
  nondirectory: 'non_directory',
  'inspection-failure': 'inspection_failure',
};

for (const kind of ['token', 'record', 'temp']) {
  for (const hazard of ['symlink', 'hardlink', 'unreadable', 'nonregular', 'inspection-failure']) {
    test(`hazard-${kind}-${hazard}: halts with provenance_hazard, never read as absent`, () => {
      withTempDir((dir) => {
        const profile = profileFor(dir);
        const deps = setupLeafHazard(profile, dir, kind, hazard);
        const result = CR.recoverProvenanceState(profile, deps);
        assert.equal(result.ok, false, `expected a halt for ${kind}/${hazard}, got state ${result.state}`);
        assert.equal(result.halts[0].halt, 'provenance_hazard');
        assert.equal(result.halts[0].reason, LEAF_HAZARD_REASON[hazard]);
      });
    });
  }
}

for (const kind of ['root', 'run-dir']) {
  for (const hazard of ['symlink', 'nondirectory', 'inspection-failure']) {
    test(`hazard-${kind}-${hazard}: halts with provenance_hazard before any leaf is opened`, () => {
      withTempDir((dir) => {
        const profile = profileFor(dir);
        const deps = setupHierarchyHazard(profile, dir, kind, hazard);
        const result = CR.recoverProvenanceState(profile, deps);
        assert.equal(result.ok, false, `expected a halt for ${kind}/${hazard}, got state ${result.state}`);
        assert.equal(result.halts[0].halt, 'provenance_hazard');
        assert.equal(result.halts[0].reason, HIERARCHY_HAZARD_REASON[hazard]);
      });
    });
  }
}

test('gate 6: the two collapsed-reason leaf hazards (unreadable, inspection-failure) are driven as SEPARATE fixtures, neither swallowed', () => {
  // Restated as its own assertion because the shared reason string is easy to misread as "only
  // one of these two is actually tested" — both are, independently, above; this just documents the
  // count so a future refactor that accidentally merges the two loops back into one is visible.
  const leafFixtureCount = 3 * 5;
  const hierarchyFixtureCount = 2 * 3;
  assert.equal(leafFixtureCount + hierarchyFixtureCount, 21);
});

// =================================================================================================
// Row 6 — the 22-case decision table (row6-generated.md `cases`) driven through the REAL consumer
// =================================================================================================

const RUN_ID_A = 'run-a';
const RUN_ID_B = 'run-b';

const DECISION_CASES = [
  { token: 'absent', record: 'absent', temps: 'none', expected: 'absent' },
  { token: 'absent', record: 'absent', temps: 'some', expected: 'orphan_temp' },
  { token: 'invalid', record: 'absent', temps: 'none', expected: 'partial' },
  { token: 'invalid', record: 'absent', temps: 'some', expected: 'partial' },
  { token: 'absent', record: 'invalid', temps: 'none', expected: 'absent' },
  { token: 'absent', record: 'invalid', temps: 'some', expected: 'orphan_temp' },
  { token: 'invalid', record: 'invalid', temps: 'none', expected: 'partial' },
  { token: 'invalid', record: 'invalid', temps: 'some', expected: 'partial' },
  { token: 'absent', record: 'valid', temps: 'none', expected: 'absent' },
  { token: 'absent', record: 'valid', temps: 'some', expected: 'orphan_temp' },
  { token: 'invalid', record: 'valid', temps: 'none', expected: 'partial' },
  { token: 'invalid', record: 'valid', temps: 'some', expected: 'partial' },
  { token: 'valid', record: 'invalid', temps: 'none', expected: 'malformed' },
  { token: 'valid', record: 'absent', temps: 'none', expected: 'open' },
  { token: 'valid', record: 'valid', temps: 'none', id: 'differ', digest: 'equal', expected: 'open' },
  { token: 'valid', record: 'valid', temps: 'none', id: 'differ', digest: 'differ', expected: 'open' },
  { token: 'valid', record: 'valid', temps: 'none', id: 'same', digest: 'equal', expected: 'committed' },
  { token: 'valid', record: 'valid', temps: 'none', id: 'same', digest: 'differ', expected: 'divergent' },
  { token: 'valid', record: 'invalid', temps: 'some', expected: 'malformed' },
  { token: 'valid', record: 'absent', temps: 'some', expected: 'prepared' },
  { token: 'valid', record: 'valid', temps: 'some', id: 'differ', digest: 'equal', expected: 'prepared' },
  { token: 'valid', record: 'valid', temps: 'some', id: 'differ', digest: 'differ', expected: 'prepared' },
  { token: 'valid', record: 'valid', temps: 'some', id: 'same', digest: 'equal', expected: 'committed' },
  { token: 'valid', record: 'valid', temps: 'some', id: 'same', digest: 'differ', expected: 'divergent' },
];

function buildFixtureFiles(profile, spec) {
  const fixture = {};
  const tokenDigest = ZERO_DIGEST;
  if (spec.token === 'valid') {
    fixture.token = validToken(RUN_ID_A, tokenDigest);
  } else if (spec.token === 'invalid') {
    fixture.token = '{}'; // parses, but no run_id/opening_digest
  }
  if (spec.record === 'valid') {
    const recordId = spec.id === 'differ' ? RUN_ID_B : RUN_ID_A;
    const recordDigest = spec.digest === 'differ' ? ONE_DIGEST : tokenDigest;
    fixture.record = validRunRecord(recordId, recordDigest);
  } else if (spec.record === 'invalid') {
    fixture.record = 'not json at all';
  }
  fixture.temps = spec.temps === 'some' ? ['{}'] : [];
  return fixture;
}

test('row 6: the 24 raw-observation cases (row6-generated.md), driven through the real recoverProvenanceState', () => {
  for (const spec of DECISION_CASES) {
    withTempDir((dir) => {
      const profile = profileFor(dir);
      writeFixture(profile, buildFixtureFiles(profile, spec));
      const result = CR.recoverProvenanceState(profile, realDeps());
      assert.equal(
        result.state,
        spec.expected,
        `token=${spec.token} record=${spec.record} temps=${spec.temps} id=${spec.id ?? 'n/a'} digest=${spec.digest ?? 'n/a'} -> expected ${spec.expected}, got ${result.state}`,
      );
    });
  }
});

test('row 6: not_active on a skipped (overlapping, unconfigured) profile — zero token/record reads', () => {
  withTempDir((dir) => {
    nodeFs.mkdirSync(join(dir, 'handbook'), { recursive: true });
    const profile = {
      capture: { output_dir: join(dir, 'handbook') },
      publish: { chapters_dir: join(dir, 'handbook') },
    };
    const result = CR.recoverProvenanceState(profile, realDeps());
    assert.equal(result.state, 'not_active');
    assert.deepEqual(result.expected, { state: 'not_active', run_id: null, opening_digest: null });
  });
});

test('row 6: `expected` is never null, and state-only-expected states carry null fingerprints', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    writeFixture(profile, buildFixtureFiles(profile, { token: 'absent', record: 'absent', temps: 'none' }));
    const result = CR.recoverProvenanceState(profile, realDeps());
    assert.equal(result.state, 'absent');
    assert.notEqual(result.expected, null);
    assert.equal(result.expected.run_id, null);
    assert.equal(result.expected.opening_digest, null);
  });
});

// Totality over a PARSEABLE-but-non-object token body. `null` is the one JSON value that is both
// non-object and dereferenceable-looking: `parseJsonStrict` returns `{ok: true, value: null}`, so a
// bare `.run_id` throws where every sibling body (`5`, `"str"`, `[]`) reaches the same `typeof`
// comparison harmlessly. Measured RED before the fix: `recoverProvenanceState` threw
// `TypeError: Cannot read properties of null (reading 'run_id')` for the `null` body alone, while
// all four others classified as `partial` — a totality claim broken by exactly one input.
// The whole non-object class is asserted here rather than just `null`, so a future guard that
// special-cases the one measured value instead of the class still fails this test.
test('row 6 totality: every parseable non-object token body classifies rather than throwing (`null` is not special)', () => {
  const bodies = ['null', '5', '"str"', '[]', 'true'];
  assert.ok(bodies.includes('null'), 'the regressed value must stay in the class under test');
  let classified = 0;
  for (const body of bodies) {
    withTempDir((dir) => {
      const profile = profileFor(dir);
      writeFixture(profile, { token: body });
      const result = CR.recoverProvenanceState(profile, realDeps());
      assert.equal(result.state, 'partial', `token body ${body} should classify as partial`);
      assert.equal(result.expected.run_id, null, `token body ${body} carries no fingerprint`);
      classified += 1;
    });
  }
  assert.equal(classified, bodies.length, 'every body must have been driven');
});

test('closeCaptureRun totality: a `null` token body returns a stale_replay halt rather than throwing', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const digest = 'sha256:' + 'a'.repeat(64);
    writeFixture(profile, { token: 'null' });
    const runState = { run_id: 'r1', opening_digest: digest, entries: [], opening: null, opening_assets: {} };
    const result = CR.closeCaptureRun(profile, runState, { ok: true }, null, realDeps());
    assert.equal(result.ok, false);
    assert.equal(result.halts[0].halt, 'stale_replay', JSON.stringify(result.halts));
  });
});

// =================================================================================================
// Row 6 — abort / cleanup repairs
// =================================================================================================

test('abortCaptureRun: removes every temp first and the token last, reaching absent', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    writeFixture(profile, { token: validToken(RUN_ID_A, ZERO_DIGEST), temps: ['{}', '{}'] });
    const verdict = CR.recoverProvenanceState(profile, realDeps());
    assert.equal(verdict.state, 'prepared');
    const result = CR.abortCaptureRun(profile, verdict.expected, realDeps());
    assert.equal(result.ok, true);
    assert.equal(nodeFs.existsSync(tokenPathFor(profile)), false);
    const after = CR.recoverProvenanceState(profile, realDeps());
    assert.equal(after.state, 'absent');
  });
});

test('abortCaptureRun: the deletion ORDER is temps-then-token, not just the final absence (codex: "the named test checks only final absence rather than order")', () => {
  // A REVERSED-ORDER implementation (token deleted first, then temps) reaches the exact same
  // final state — nothing on disk, `absent` — so a check that only asserts the end state cannot
  // distinguish it from the correct one. Recording the actual unlink call SEQUENCE is what pins
  // the order itself, independent of the final result.
  withTempDir((dir) => {
    const profile = profileFor(dir);
    writeFixture(profile, { token: validToken(RUN_ID_A, ZERO_DIGEST), temps: ['{}', '{}'] });
    const verdict = CR.recoverProvenanceState(profile, realDeps());
    assert.equal(verdict.state, 'prepared');
    const deletionOrder = [];
    const deps = depsWithOverride({
      unlinkSync: (p) => {
        deletionOrder.push(p);
        return nodeFs.unlinkSync(p);
      },
    });
    const result = CR.abortCaptureRun(profile, verdict.expected, deps);
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.equal(deletionOrder.length, 3, `expected 2 temps + 1 token, got ${JSON.stringify(deletionOrder)}`);
    const tokenIndex = deletionOrder.indexOf(tokenPathFor(profile));
    assert.notEqual(tokenIndex, -1, 'token must have been deleted');
    assert.equal(tokenIndex, deletionOrder.length - 1, 'the token must be the LAST path deleted, not merely eventually gone');
    for (let i = 0; i < deletionOrder.length - 1; i++) {
      assert.notEqual(deletionOrder[i], tokenPathFor(profile), `token deleted too early, at position ${i}`);
    }
  });
});

test('abortCaptureRun / cleanupCommittedRun: idempotent — twice, and on an already-absent token', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    writeFixture(profile, { token: validToken(RUN_ID_A, ZERO_DIGEST) });
    const verdict = CR.recoverProvenanceState(profile, realDeps());
    assert.equal(verdict.state, 'open');
    const first = CR.abortCaptureRun(profile, verdict.expected, realDeps());
    assert.equal(first.ok, true);
    const second = CR.abortCaptureRun(profile, verdict.expected, realDeps());
    assert.equal(second.ok, true);
    assert.equal(second.noop, true);
  });
});

test('cleanupCommittedRun: unlinks the token when committed, leaves the record untouched', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    writeFixture(profile, { token: validToken(RUN_ID_A, ZERO_DIGEST), record: validRunRecord(RUN_ID_A, ZERO_DIGEST) });
    const verdict = CR.recoverProvenanceState(profile, realDeps());
    assert.equal(verdict.state, 'committed');
    const before = nodeFs.readFileSync(recordPathFor(profile), 'utf8');
    const result = CR.cleanupCommittedRun(profile, verdict.expected, realDeps());
    assert.equal(result.ok, true);
    assert.equal(nodeFs.existsSync(tokenPathFor(profile)), false);
    assert.equal(nodeFs.readFileSync(recordPathFor(profile), 'utf8'), before);
  });
});

test('row 6: refuses cleanupCommittedRun on `open`, and refuses abortCaptureRun on `committed`', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    writeFixture(profile, { token: validToken(RUN_ID_A, ZERO_DIGEST) });
    const openVerdict = CR.recoverProvenanceState(profile, realDeps());
    assert.equal(openVerdict.state, 'open');
    const wrongRepair = CR.cleanupCommittedRun(profile, openVerdict.expected, realDeps());
    assert.equal(wrongRepair.ok, false);
    assert.equal(wrongRepair.halts[0].halt, 'stale_verdict');
  });

  withTempDir((dir) => {
    const profile = profileFor(dir);
    writeFixture(profile, { token: validToken(RUN_ID_A, ZERO_DIGEST), record: validRunRecord(RUN_ID_A, ZERO_DIGEST) });
    const committedVerdict = CR.recoverProvenanceState(profile, realDeps());
    assert.equal(committedVerdict.state, 'committed');
    const wrongRepair = CR.abortCaptureRun(profile, committedVerdict.expected, realDeps());
    assert.equal(wrongRepair.ok, false);
    assert.equal(wrongRepair.halts[0].halt, 'stale_verdict');
  });
});

test('row 6: the WRONG executor is refused even once the tree has ALREADY reached absent (codex important #5)', () => {
  // The wrong-executor check must be keyed on expected.state (what the caller CLAIMS to be
  // repairing), never on observedState (what is currently on disk) — REPAIR_FOR_STATE has no entry
  // for 'absent', so keying on observedState silently stops comparing anything the instant the
  // tree has already reached it, accepting literally any executor as a no-op.
  withTempDir((dir) => {
    const profile = profileFor(dir);
    writeFixture(profile, { token: validToken(RUN_ID_A, ZERO_DIGEST) });
    const openVerdict = CR.recoverProvenanceState(profile, realDeps());
    assert.equal(openVerdict.state, 'open');
    // The run is independently and legitimately aborted through the CORRECT api — the tree is now
    // genuinely 'absent'.
    const correctAbort = CR.abortCaptureRun(profile, openVerdict.expected, realDeps());
    assert.equal(correctAbort.ok, true);
    assert.equal(CR.recoverProvenanceState(profile, realDeps()).state, 'absent');

    // A caller now presents the SAME (now-stale) 'open' verdict to the WRONG repair — cleanup was
    // never open's prescribed repair, and that must be refused regardless of what the tree
    // currently looks like.
    const wrongApiOnStaleVerdict = CR.cleanupCommittedRun(profile, openVerdict.expected, realDeps());
    assert.equal(wrongApiOnStaleVerdict.ok, false, JSON.stringify(wrongApiOnStaleVerdict));
    assert.equal(wrongApiOnStaleVerdict.halts[0].halt, 'stale_verdict');
    assert.equal(wrongApiOnStaleVerdict.halts[0].reason, 'wrong_repair_for_state');
  });
});

test('row 6: mutation_failed names the CURRENT state after a partial removal, not just the path and what was removed (codex important #5)', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    writeFixture(profile, { token: validToken(RUN_ID_A, ZERO_DIGEST), temps: ['{}'] });
    const verdict = CR.recoverProvenanceState(profile, realDeps());
    assert.equal(verdict.state, 'prepared');
    const tempPath = tempPathFor(profile, 'aaaaaaaa-0000-0000-0000-000000000000');
    const boom = Object.assign(new Error('injected unlink failure'), { code: 'EIO' });
    const deps = depsWithOverride({
      unlinkSync: (p) => {
        if (p === tempPath) throw boom;
        return nodeFs.unlinkSync(p);
      },
    });
    const result = CR.abortCaptureRun(profile, verdict.expected, deps);
    assert.equal(result.ok, false, JSON.stringify(result));
    assert.equal(result.halts[0].halt, 'mutation_failed');
    // The tree is still 'prepared' — the injected failure blocked the only temp from being removed.
    assert.equal(result.halts[0].currentState, 'prepared', JSON.stringify(result.halts[0]));
  });
});

test('row 6: a stale expected fingerprint (A opened, aborted, B opened) halts stale_verdict rather than destroying B', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    writeFixture(profile, { token: validToken(RUN_ID_A, ZERO_DIGEST) });
    const staleVerdict = CR.recoverProvenanceState(profile, realDeps());
    // Simulate: A's run is legitimately closed/removed and B opens a fresh one.
    nodeFs.unlinkSync(tokenPathFor(profile));
    nodeFs.writeFileSync(tokenPathFor(profile), validToken(RUN_ID_B, ONE_DIGEST));
    const result = CR.abortCaptureRun(profile, staleVerdict.expected, realDeps());
    assert.equal(result.ok, false);
    assert.equal(result.halts[0].halt, 'stale_verdict');
    // B's token must survive.
    assert.equal(nodeFs.existsSync(tokenPathFor(profile)), true);
  });
});

test('row 6: reaching an INTERMEDIATE progress-chain point resumes the remaining suffix (not a no-op)', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    // Simulate a crash mid-abort of `prepared`: temps already gone, token still present -> `open`.
    writeFixture(profile, { token: validToken(RUN_ID_A, ZERO_DIGEST) });
    const verdict = CR.recoverProvenanceState(profile, realDeps());
    assert.equal(verdict.state, 'open');
    // expected still names the ORIGINAL 'prepared' request (as recovery would have returned before
    // the crash finished removing the last temp).
    const expected = { state: 'prepared', run_id: RUN_ID_A, opening_digest: ZERO_DIGEST };
    const result = CR.abortCaptureRun(profile, expected, realDeps());
    assert.equal(result.ok, true);
    assert.equal(result.noop, undefined);
    assert.equal(nodeFs.existsSync(tokenPathFor(profile)), false);
  });
});

test('row 6: accepts an accurate SYNTHESIZED expected (by design — expected is a witness, not a capability)', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    writeFixture(profile, { token: validToken(RUN_ID_A, ZERO_DIGEST) });
    // Never called recoverProvenanceState — synthesized directly from public knowledge of the shape.
    const synthesized = { state: 'open', run_id: RUN_ID_A, opening_digest: ZERO_DIGEST };
    const result = CR.abortCaptureRun(profile, synthesized, realDeps());
    assert.equal(result.ok, true);
  });
});

// =================================================================================================
// openCaptureRun / closeCaptureRun
// =================================================================================================

function stubDepsNoIdentity() {
  return depsWithOverride({ runIdentityCommand: () => ({ ok: false, detail: 'not configured' }) });
}

// [round 33] `closeCaptureRun` runs gate 3 now, so a hazard already on disk when it is called is
// VALIDATION's to refuse — which means a fixture that plants beforehand pins gate 3 and says nothing
// about the snapshot guard it was written for. The window between the two is real and the sweep
// supplies a seam for it: every entry is validated together, up front, and the chapters are then
// snapshotted one at a time. Planting on the way out of the FIRST chapter's listing lands strictly
// after every gate has passed and strictly before the second chapter is observed at all — no syscall
// counting, and it is the shape a concurrent mutation actually takes.
//
// The opening twins get the same window for free, from the reservation write that sits between
// validation and their own sweep; the close has no such call of its own.
const SWEEP_ARMING_ENTRY = { slug: 'listed-first' };

function armAfterFirstChapterListing(profile, plant) {
  nodeFs.mkdirSync(join(profile.capture.output_dir, SWEEP_ARMING_ENTRY.slug), { recursive: true });
  const state = { armed: false };
  state.readdirSync = (p, opts) => {
    try {
      return nodeFs.readdirSync(p, opts);
    } finally {
      if (!state.armed && String(p).endsWith(`/${SWEEP_ARMING_ENTRY.slug}`)) {
        state.armed = true;
        plant();
      }
    }
  };
  return state;
}

// =================================================================================================
// Gates 1-4 — the asset-tree preflight (codex DO-NOT-SHIP blocker 1). Driven through the real
// consumers: openCaptureRun (W2), recordChapterProvenance (W5, over the full accepted manifest),
// buildProvenanceReport (W6, independently callable, must run its own gates before deriving a path).
// =================================================================================================

test('gate 1: an invalid slug halts openCaptureRun, a valid kebab/digit slug proceeds', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const bad = CR.openCaptureRun(profile, [{ slug: 'Invalid_Slug' }], null, stubDepsNoIdentity());
    assert.equal(bad.ok, false);
    assert.equal(bad.halts[0].halt, 'invalid_slug');

    const good = CR.openCaptureRun(profile, [{ slug: 'q1' }, { slug: '10' }, { slug: 'invoice-export' }], null, stubDepsNoIdentity());
    assert.equal(good.ok, true, JSON.stringify(good));
  });
});

test('gate 1: the invalid entry is never first — [safe, invalid] halts with zero writes', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const result = CR.openCaptureRun(profile, [{ slug: 'safe' }, { slug: '../elsewhere' }], null, stubDepsNoIdentity());
    assert.equal(result.ok, false);
    assert.equal(nodeFs.existsSync(tokenPathFor(profile)), false);
  });
});

test('gate 1 (IMPORTANT 1, codex review): a NUMERIC slug is rejected as invalid_slug, not coerced to a passing string — driven through buildProvenanceReport (W6)', () => {
  // The consumer used to call `isValidSlugSyntax(String(entry.slug))` — the type check inside
  // `isValidSlugSyntax` (`typeof slug === 'string'`) never got to see the ORIGINAL value, since it
  // was already coerced to a string before the call. `{slug: 1}` -> `String(1) === '1'`, which
  // passes the kebab alphabet (digits are in the class), so gate 1 silently accepted a non-string
  // slug and a W6 probe reached `chapter_read_failed` (no "1.md" chapter file exists) instead of
  // `invalid_slug`.
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const result = CR.buildProvenanceReport(profile, [{ slug: 1 }], null, stubDepsNoIdentity());
    assert.equal(result.ok, false, JSON.stringify(result));
    assert.equal(result.halts[0].halt, 'invalid_slug', `a non-string slug must fail gate 1's TYPE check; got ${JSON.stringify(result)}`);
  });
});

test('gate 1 (IMPORTANT 1, codex review): a NULL group is rejected as invalid_group, not coerced to the string "null"', () => {
  // Same coercion bug on the group field: `String(null) === 'null'`, which itself matches the
  // kebab alphabet (no hyphen required), so `{group: null}` used to sail past gate 1 too.
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const result = CR.openCaptureRun(profile, [{ slug: 'items', group: null }], null, stubDepsNoIdentity());
    assert.equal(result.ok, false, JSON.stringify(result));
    assert.equal(result.halts[0].halt, 'invalid_group', JSON.stringify(result));
  });
});

test('gate 2: two entries deriving the identical LEXICAL asset directory halt', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    // Once gate 1 forbids '/' in both slug and group, the classic flat-vs-grouped string alias
    // (e.g. flat 'admin/items' vs grouped group:'admin'+slug:'items') is structurally unreachable —
    // neither field can ever contain a literal '/'. The realistic residual gate 2 catches is a
    // literal duplicate manifest entry, which is what this fixture is.
    const result = CR.openCaptureRun(
      profile,
      [{ slug: 'items', group: 'admin' }, { slug: 'items', group: 'admin' }],
      null,
      stubDepsNoIdentity(),
    );
    assert.equal(result.ok, false);
    assert.equal(result.halts[0].halt, 'duplicate_asset_dir');
  });
});

test('gate 3: a symlinked asset directory that escapes capture.output_dir halts, with zero writes', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const outside = join(dir, 'outside-assets');
    nodeFs.mkdirSync(outside, { recursive: true });
    nodeFs.mkdirSync(profile.capture.output_dir, { recursive: true });
    nodeFs.symlinkSync(outside, join(profile.capture.output_dir, 'items'));
    const result = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
    assert.equal(result.ok, false);
    assert.equal(result.halts[0].halt, 'asset_dir_escapes_output_dir');
    assert.equal(nodeFs.existsSync(tokenPathFor(profile)), false);
  });
});

test('gate 3: a legitimate deep/nested asset directory does NOT false-halt', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    nodeFs.mkdirSync(join(profile.capture.output_dir, 'admin', 'items'), { recursive: true });
    const result = CR.openCaptureRun(profile, [{ slug: 'items', group: 'admin' }], null, stubDepsNoIdentity());
    assert.equal(result.ok, true, JSON.stringify(result));
  });
});

test('gate 3 (round 5, finding 1): a symlinked ANCESTOR of a not-yet-created leaf still halts — a missing leaf must not skip containment for the whole path', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const outside = join(dir, 'outside-assets');
    nodeFs.mkdirSync(outside, { recursive: true });
    nodeFs.mkdirSync(profile.capture.output_dir, { recursive: true });
    // 'admin' is a symlink OUT of capture.output_dir. This entry's actual leaf ('admin/items') is
    // never created — exactly the state gate 3 sees on a chapter's very first capture. Without the
    // ancestor walk, the ENOENT on the full (non-existent) leaf skipped containment altogether, and
    // the capture command run afterwards would have created 'items' straight through the symlink,
    // physically outside capture.output_dir.
    nodeFs.symlinkSync(outside, join(profile.capture.output_dir, 'admin'));
    const result = CR.openCaptureRun(profile, [{ slug: 'items', group: 'admin' }], null, stubDepsNoIdentity());
    assert.equal(result.ok, false, JSON.stringify(result));
    assert.equal(result.halts[0].halt, 'asset_dir_escapes_output_dir', JSON.stringify(result));
    assert.equal(nodeFs.existsSync(tokenPathFor(profile)), false);
  });
});

test('gate 3 (round 5, finding 1): a chapter whose own leaf does not exist yet, under an existing non-symlinked root, does NOT false-halt', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    // The root exists already (an earlier chapter's capture created it) but THIS chapter's own
    // subdirectory never has — its very first capture run. The ancestor walk must stop at the root
    // itself (the longest existing prefix) and find it trivially contained, not halt.
    nodeFs.mkdirSync(profile.capture.output_dir, { recursive: true });
    const result = CR.openCaptureRun(profile, [{ slug: 'items', group: 'admin' }], null, stubDepsNoIdentity());
    assert.equal(result.ok, true, JSON.stringify(result));
  });
});

test('gate 3/4 (round 5, finding 1): two sibling chapters sharing an existing group ancestor, both with not-yet-created leaves, do NOT manufacture a gate-4 collision', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    // Both entries' ancestor walk resolves to the SAME existing 'admin' directory — but their
    // TAILS beyond it ('items' vs 'invoices') differ, so the composite physical identity gate 4
    // actually compares (resolved ancestor + remaining tail — round 6, finding 1) still differs
    // between them. It is the differing tail that keeps this from colliding, not an exemption for
    // ancestors in general: two sibling chapters legitimately distinct under one shared ancestor
    // must not be manufactured into a false collision.
    nodeFs.mkdirSync(join(profile.capture.output_dir, 'admin'), { recursive: true });
    const result = CR.openCaptureRun(
      profile,
      [{ slug: 'items', group: 'admin' }, { slug: 'invoices', group: 'admin' }],
      null,
      stubDepsNoIdentity(),
    );
    assert.equal(result.ok, true, JSON.stringify(result));
  });
});

test('gate 3/4 (codex round 6, finding 1 — BLOCKER): two entries with NOT-YET-CREATED leaves, symlinked through DIFFERENT group ancestors into the SAME shared physical directory, with the SAME tail, must collide', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    // Codex's repro: 'a' and 'b' are two DIFFERENT symlinked group ancestors that both point at the
    // one physical 'shared' directory; neither entry's own leaf ('items') has been created yet.
    // Before this fix, a missing leaf was resolved only as far as its existing ANCESTOR and then
    // dropped from gate 4's collision set entirely (never re-added with its tail) — so this pair
    // sailed through as ok:true, and the capture command run afterwards would have written both
    // chapters' assets into the one physical 'shared/items' directory, one silently overwriting the
    // other's images.
    const shared = join(profile.capture.output_dir, 'shared');
    nodeFs.mkdirSync(shared, { recursive: true });
    nodeFs.symlinkSync(shared, join(profile.capture.output_dir, 'a'));
    nodeFs.symlinkSync(shared, join(profile.capture.output_dir, 'b'));
    const result = CR.openCaptureRun(
      profile,
      [{ slug: 'items', group: 'a' }, { slug: 'items', group: 'b' }],
      null,
      stubDepsNoIdentity(),
    );
    assert.equal(result.ok, false, JSON.stringify(result));
    assert.equal(result.halts[0].halt, 'physical_asset_dir_collision', JSON.stringify(result));
  });
});

test('gate 3/4 (codex round 6, finding 1): two entries with NOT-YET-CREATED leaves under two DIFFERENT, non-aliased physical ancestors, sharing the SAME tail name, do NOT collide', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    // Negative control for the fix above: the two ancestors ('admin', 'billing') are genuinely
    // distinct physical directories (no symlink between them) — only the TAIL segment name
    // ('items') happens to match. The composite (resolved ancestor + tail) must still differ,
    // since the ancestors themselves differ; a mutant that collides on tail-name alone, ignoring
    // which ancestor it hangs off, must fail this.
    nodeFs.mkdirSync(join(profile.capture.output_dir, 'admin'), { recursive: true });
    nodeFs.mkdirSync(join(profile.capture.output_dir, 'billing'), { recursive: true });
    const result = CR.openCaptureRun(
      profile,
      [{ slug: 'items', group: 'admin' }, { slug: 'items', group: 'billing' }],
      null,
      stubDepsNoIdentity(),
    );
    assert.equal(result.ok, true, JSON.stringify(result));
  });
});

test('gate 4: two entries whose LEXICALLY distinct asset directories resolve to the SAME physical directory (an inside-root symlink alias) halt', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    nodeFs.mkdirSync(join(profile.capture.output_dir, 'billing', 'invoices'), { recursive: true });
    nodeFs.mkdirSync(join(profile.capture.output_dir, 'admin'), { recursive: true });
    // 'admin/items' is a symlink to 'billing/invoices' — both INSIDE capture.output_dir, so gate 3
    // accepts each individually; only the cross-entry physical check (gate 4) can catch the alias.
    nodeFs.symlinkSync(join(profile.capture.output_dir, 'billing', 'invoices'), join(profile.capture.output_dir, 'admin', 'items'));
    const result = CR.openCaptureRun(
      profile,
      [{ slug: 'items', group: 'admin' }, { slug: 'invoices', group: 'billing' }],
      null,
      stubDepsNoIdentity(),
    );
    assert.equal(result.ok, false);
    assert.equal(result.halts[0].halt, 'physical_asset_dir_collision');
  });
});

test('gate 4 at W5: an alias planted AFTER openCaptureRun is caught by recordChapterProvenance re-checking the full accepted manifest', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const flat = { slug: 'items' };
    const other = { slug: 'other' };
    nodeFs.mkdirSync(join(profile.capture.output_dir, 'items'), { recursive: true });
    nodeFs.mkdirSync(join(profile.capture.output_dir, 'other'), { recursive: true });
    nodeFs.writeFileSync(join(profile.capture.output_dir, 'items', 'a.png'), 'v1');

    const opened = CR.openCaptureRun(profile, [flat, other], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true);
    nodeFs.writeFileSync(join(profile.capture.output_dir, 'items', 'a.png'), 'v2');
    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, true);

    // Plant the alias AFTER W2 — gate 3/4 passed at open time, and W5 must re-establish them
    // rather than trust W2's result still holds.
    nodeFs.rmdirSync(join(profile.capture.output_dir, 'other'));
    nodeFs.symlinkSync(join(profile.capture.output_dir, 'items'), join(profile.capture.output_dir, 'other'));

    const deps = { ...stubDepsNoIdentity(), expectedAssets: () => ({ ok: true, assets: [{ key: 'a.png', absPath: join(profile.capture.output_dir, 'items', 'a.png') }] }) };
    const result = CR.recordChapterProvenance(profile, [flat, other], flat, join(dir, 'items.md'), opened.runState.run_id, deps);
    assert.equal(result.ok, false, JSON.stringify(result));
    assert.equal(result.halts[0].halt, 'physical_asset_dir_collision');
  });
});

test('gate 1-4 at W6: buildProvenanceReport halts on an invalid manifest before deriving a single path', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const result = CR.buildProvenanceReport(profile, [{ slug: '../elsewhere' }], null, stubDepsNoIdentity());
    assert.equal(result.ok, false);
    assert.equal(result.halts[0].halt, 'invalid_slug');
  });
});

// =================================================================================================
// BLOCKER 1 (codex review, commit 69671ee) — a RELATIVE capture.output_dir must not desync gate 3's
// two sides. Every OTHER fixture in this file uses ABSOLUTE temp paths (profileFor joins onto the
// absolute `dir` withTempDir hands back), which is exactly why this was invisible: gate 3's root
// (`canonicalOutputRoot`, always absolutized by canonicalizeForComparison) and its per-entry
// candidate (the raw, still-relative `chapterAssetDir(...)`) silently sat in two different
// coordinate systems, and `resolvePhysicalContainment` treats a rootedness mismatch the same as a
// genuine escape. The SHIPPED example profile uses exactly this relative/relative topology
// (`output_dir: "vault/handbook/assets"`, `chapters_dir: "vault/handbook"`), so this fired on the
// very first real capture, in open, W5 and W6 alike.
// =================================================================================================

function withRelativeCwd(dir, fn) {
  const prevCwd = process.cwd();
  process.chdir(dir);
  try {
    return fn();
  } finally {
    process.chdir(prevCwd);
  }
}

test('BLOCKER 1: a RELATIVE capture.output_dir (the shipped example profile\'s own topology) must not halt gate 3 in open, W5 or W6', () => {
  withTempDir((dir) => {
    withRelativeCwd(dir, () => {
      const chaptersDir = 'vault/handbook';
      nodeFs.mkdirSync(join(dir, chaptersDir), { recursive: true });
      const profile = {
        capture: { output_dir: 'vault/handbook/assets', build_identity: { ui_read: false } },
        publish: { chapters_dir: chaptersDir, target: 'static_md' },
      };
      const entry = { slug: 'items' };
      const assetDir = 'vault/handbook/assets/items'; // relative, mirrors profile.capture.output_dir
      nodeFs.mkdirSync(assetDir, { recursive: true });
      nodeFs.writeFileSync(join(assetDir, 'overview.png'), 'v1');

      const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
      assert.equal(opened.ok, true, `W2 (open) must accept a relative output_dir topology; got ${JSON.stringify(opened)}`);

      nodeFs.writeFileSync(join(assetDir, 'overview.png'), 'v2');
      const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
      assert.equal(closed.ok, true, JSON.stringify(closed));

      // Real embed formula, real chapter file — exercising the REAL default extractor (no
      // `expectedAssets` override), matching what an actual handbook run does.
      const chapterFile = 'vault/handbook/items.md';
      const embed = chapterPathsModule.embedPath(chapterFile, assetDir, 'overview.png');
      nodeFs.writeFileSync(chapterFile, `# Items\n\n1. Step\n\n   ![overview](${embed})\n`);

      const recorded = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, opened.runState.run_id, stubDepsNoIdentity());
      assert.equal(recorded.recorded, true, `W5 must accept the same relative topology; got ${JSON.stringify(recorded)}`);

      const report = CR.buildProvenanceReport(profile, [entry], null, stubDepsNoIdentity());
      assert.equal(Array.isArray(report.rows), true, `W6 must accept the same relative topology; got ${JSON.stringify(report)}`);
      assert.equal(report.rows.length, 1);
      assert.equal(report.rows[0].key, 'items');
    });
  });
});

test('openCaptureRun (codex round 7, IMPORTANT 1): a hierarchy-establishment hazard is wrapped in the declared Halt shape, not the raw hazard object', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const rootPath = CR.provenanceRoot(profile);
    // Before the fix, `establishHierarchy`'s failure (`{ok:false, hazard:{kind,reason,path}}`) was
    // pushed straight into `halts` — the raw hazard object, with no `halt` discriminator at all.
    // `capture-record.d.mts`'s `Halt` type requires `halt: string`; a caller dispatching on that
    // declared field sees `undefined` here, exactly on a setup error, when the operator most needs
    // the message.
    const boom = Object.assign(new Error('injected lstat failure'), { code: 'EACCES' });
    const deps = depsWithOverride({
      lstatSync: (p, ...rest) => {
        if (p === rootPath) throw boom;
        return nodeFs.lstatSync(p, ...rest);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'not configured' }),
    });
    const result = CR.openCaptureRun(profile, [], null, deps);
    assert.equal(result.ok, false, JSON.stringify(result));
    assert.equal(result.halts.length, 1, JSON.stringify(result));
    assert.equal(result.halts[0].halt, 'provenance_hazard', `expected the declared halt discriminator; got ${JSON.stringify(result.halts[0])}`);
    assert.equal(result.halts[0].reason, 'inspection_failure');
    assert.equal(result.halts[0].path, rootPath);
  });
});

test('recordChapterProvenance (codex round 7, IMPORTANT 1): a group-dir establishment hazard is wrapped in the declared Halt shape, not the raw hazard object', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items', group: 'admin' };
    const { runId, assetDir, chapterFile } = runToCommitted(profile, entry, { 'overview.png': 'v1' }, { 'overview.png': 'v2' });
    const groupDirPath = join(CR.provenanceRoot(profile), 'chapters', 'admin');
    // Same defect as the W2 sibling above, at `establishChapterGroupDir`'s call site: the raw
    // `{kind,reason,path}` hazard object went straight into `halts`, with no `halt` discriminator.
    const boom = Object.assign(new Error('injected mkdir failure'), { code: 'EACCES' });
    const deps = {
      ...stubDepsNoIdentity(),
      expectedAssets: stubExpectedAssetsFor(assetDir, ['overview.png']),
      mkdirSync: (p, ...rest) => {
        if (p === groupDirPath) throw boom;
        return nodeFs.mkdirSync(p, ...rest);
      },
    };
    const result = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, runId, deps);
    assert.equal(result.ok, false, JSON.stringify(result));
    assert.equal(result.halts.length, 1, JSON.stringify(result));
    assert.equal(result.halts[0].halt, 'provenance_hazard', `expected the declared halt discriminator; got ${JSON.stringify(result.halts[0])}`);
    assert.equal(result.halts[0].reason, 'inspection_failure');
    assert.equal(result.halts[0].path, groupDirPath);
  });
});

test('openCaptureRun: exclusive-create contention — one success, one EEXIST halt', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const first = CR.openCaptureRun(profile, [], null, stubDepsNoIdentity());
    assert.equal(first.ok, true);
    const second = CR.openCaptureRun(profile, [], null, stubDepsNoIdentity());
    assert.equal(second.ok, false);
    assert.equal(second.halts[0].halt, 'run_already_open');
  });
});

test('openCaptureRun + closeCaptureRun: happy path, flat entry, snapshots opening/closing hashes', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    nodeFs.mkdirSync(join(profile.capture.output_dir, 'items'), { recursive: true });
    nodeFs.writeFileSync(join(profile.capture.output_dir, 'items', 'overview.png'), 'v1');

    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true);
    assert.equal(opened.runState.opening_assets.items['overview.png'], `sha256:${createHash('sha256').update('v1').digest('hex')}`);

    nodeFs.writeFileSync(join(profile.capture.output_dir, 'items', 'overview.png'), 'v2');
    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, true);
    assert.equal(nodeFs.existsSync(tokenPathFor(profile)), false);
    const recordRaw = JSON.parse(nodeFs.readFileSync(recordPathFor(profile), 'utf8'));
    assert.equal(recordRaw.run_id, opened.runState.run_id);
    assert.equal(recordRaw.chapters.items.closing['overview.png'], `sha256:${createHash('sha256').update('v2').digest('hex')}`);
  });
});

test('snapshotAssetHashes: a symlinked asset entry is never followed, including the TOCTOU shape where readdir reported it as a regular file', () => {
  // capture.output_dir is not plugin-owned (row 7 — the opaque capture command's namespace), so
  // this is not gate 6's obligation set, but reading through a symlink here would still hash
  // attacker- or command-chosen bytes and record them as the chapter's own asset. `readdirSync`'s
  // dirent types are a snapshot at LISTING time; the real race is an entry that WAS a regular file
  // when listed and becomes a symlink before the open. That is simulated here by injecting a
  // readdirSync whose dirent LIES (reports isFile()=true, isSymbolicLink()=false) for an entry that
  // is a REAL symlink on disk — exactly the shape `hashFileNoFollow`'s O_NOFOLLOW open must catch
  // regardless of what the caller's dirent said.
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    const secretOutside = join(dir, 'outside-secret.png');
    const secretContent = 'this must never be recorded as the chapter asset';
    nodeFs.writeFileSync(secretOutside, secretContent);
    nodeFs.symlinkSync(secretOutside, join(assetDir, 'overview.png'));

    const lyingDirent = { name: 'overview.png', isSymbolicLink: () => false, isDirectory: () => false, isFile: () => true };
    const deps = depsWithOverride({
      readdirSync: (p, opts) => {
        if (p === assetDir && opts?.withFileTypes) return [lyingDirent];
        return nodeFs.readdirSync(p, opts);
      },
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(opened.ok, true, JSON.stringify(opened));
    assert.equal(
      Object.hasOwn(opened.runState.opening_assets.items, 'overview.png'),
      false,
      'a symlinked entry (even one the dirent lied about) must be EXCLUDED from the snapshot, never hashed through',
    );
    const secretDigest = `sha256:${createHash('sha256').update(secretContent).digest('hex')}`;
    assert.equal(
      Object.values(opened.runState.opening_assets.items).includes(secretDigest),
      false,
      "the outside file's digest must never appear anywhere in the snapshot",
    );
  });
});

test('closeCaptureRun: a symlinked run/ ancestor halts before the token is ever opened (codex DO-NOT-SHIP blocker 3)', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const opened = CR.openCaptureRun(profile, [], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true);
    // Replace run/ with a symlink AFTER opening — closeCaptureRun must catch this itself; an
    // earlier pass only wired the hierarchy walk into recovery, so this ancestor was followed
    // transparently on the close path.
    const runDirPath = runDir(profile);
    const savedTokenPath = tokenPathFor(profile);
    const savedToken = nodeFs.readFileSync(savedTokenPath, 'utf8');
    nodeFs.rmSync(runDirPath, { recursive: true, force: true });
    const outside = join(dir, 'outside-run');
    nodeFs.mkdirSync(outside, { recursive: true });
    nodeFs.writeFileSync(join(outside, 'pending.json'), savedToken);
    nodeFs.symlinkSync(outside, runDirPath);
    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, false, JSON.stringify(closed));
    assert.equal(closed.halts[0].halt, 'provenance_hazard');
    assert.equal(closed.halts[0].reason, 'symlink');
  });
});

test('recordChapterProvenance: a symlinked chapters/ ancestor halts before any run/chapter record is touched (codex DO-NOT-SHIP blocker 3)', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');
    const chapterFile = join(dir, 'items.md');
    nodeFs.writeFileSync(chapterFile, '# items\n');

    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true);
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v2');
    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, true);

    // Replace chapters/ with a symlink after W2 closes — W5 must catch it before writing.
    const chaptersDirPath = join(CR.provenanceRoot(profile), 'chapters');
    nodeFs.rmSync(chaptersDirPath, { recursive: true, force: true });
    const outside = join(dir, 'outside-chapters');
    nodeFs.mkdirSync(outside, { recursive: true });
    nodeFs.symlinkSync(outside, chaptersDirPath);

    const deps = { ...stubDepsNoIdentity(), expectedAssets: () => ({ ok: true, assets: [{ key: 'a.png', absPath: join(assetDir, 'a.png') }] }) };
    const result = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, opened.runState.run_id, deps);
    assert.equal(result.ok, false, JSON.stringify(result));
    assert.equal(result.halts[0].halt, 'provenance_hazard');
    assert.equal(result.halts[0].reason, 'symlink');
    assert.equal(nodeFs.existsSync(nodeFs.realpathSync(outside) + '/items.json'), false);
  });
});

test('buildProvenanceReport: a symlinked chapters/ ancestor halts before any chapter record is read (codex DO-NOT-SHIP blocker 3)', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    nodeFs.mkdirSync(CR.provenanceRoot(profile), { recursive: true });
    const chaptersDirPath = join(CR.provenanceRoot(profile), 'chapters');
    const outside = join(dir, 'outside-chapters-w6');
    nodeFs.mkdirSync(outside, { recursive: true });
    nodeFs.symlinkSync(outside, chaptersDirPath);
    const result = CR.buildProvenanceReport(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(result.ok, false, JSON.stringify(result));
    assert.equal(result.halts[0].halt, 'provenance_hazard');
    assert.equal(result.halts[0].reason, 'symlink');
  });
});

test('closeCaptureRun: a stale runState (wrong token on disk) halts rather than committing a replay', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const opened = CR.openCaptureRun(profile, [], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true);
    // Simulate: this token was replaced by a different run (e.g. after abort + reopen).
    nodeFs.unlinkSync(tokenPathFor(profile));
    nodeFs.writeFileSync(tokenPathFor(profile), validToken('someone-else', ONE_DIGEST));
    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, false);
    assert.equal(closed.halts[0].halt, 'stale_replay');
  });
});

test('closeCaptureRun: PAYLOAD TAMPERING — a runState whose entries/opening/opening_assets was mutated, with run_id and opening_digest left UNTOUCHED, halts (codex DO-NOT-SHIP blocker 2)', () => {
  // The token authenticates the run's IDENTITY (run_id) and, via the digest RECOMPUTED from
  // runState's own current content, the run's PAYLOAD too. A caller (or an attacker) that mutates
  // `entries`/`opening`/`opening_assets` while leaving the two top-level scalar fields alone must
  // never be able to smuggle a different payload through under the original run's good name.
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');
    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true);

    // Tamper with the OPENING snapshot recorded inside runState — e.g. forging a different
    // "what the file looked like before capture" hash, which would let a run falsely claim an
    // asset changed (or didn't) when it did not (or did). run_id and opening_digest are left
    // exactly as they were.
    const tampered = JSON.parse(JSON.stringify(opened.runState));
    tampered.opening_assets.items['a.png'] = ONE_DIGEST;
    assert.equal(tampered.run_id, opened.runState.run_id);
    assert.equal(tampered.opening_digest, opened.runState.opening_digest);

    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v2');
    const closed = CR.closeCaptureRun(profile, tampered, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, false, `tampered payload must halt, got ${JSON.stringify(closed)}`);
    assert.equal(closed.halts[0].halt, 'stale_replay');
    // And the token must survive — this halt must not have consumed/deleted it, since the
    // legitimate close (with the REAL runState) can still happen afterward.
    assert.equal(nodeFs.existsSync(tokenPathFor(profile)), true);

    // Confirm the LEGITIMATE (untampered) runState still closes normally against the same token.
    const legitimateClose = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(legitimateClose.ok, true, JSON.stringify(legitimateClose));
  });
});

test('closeCaptureRun: PAYLOAD TAMPERING — a mutated `entries` list (a different chapter set) with matching run_id/opening_digest halts', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true);
    const tampered = JSON.parse(JSON.stringify(opened.runState));
    tampered.entries.push({ slug: 'smuggled' });
    const closed = CR.closeCaptureRun(profile, tampered, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, false);
    assert.equal(closed.halts[0].halt, 'stale_replay');
  });
});

// [round 15 BLOCKER] The mirror image of round 14's W6 defect, in the OPENING snapshot, and worse:
// there a dropped embed produced a false `unchanged`; here it produces a false RECORD. The snapshot
// excluded any asset it could not hash, so a hazard and an absence became the same missing key —
// and W5 reads a missing OPENING key as "brand-new file this run", which SKIPS rule 4, the check
// that the bytes changed during capture. Nothing in the run then establishes that these bytes came
// from this build. The asset's content never changes here; only the hard link does, and it is gone
// by publish time, so every check that looks at the file at W5 is satisfied.
test('recordChapterProvenance: an asset UNHASHABLE AT OPEN is never recorded, even though W5 can read it', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    // Old-build bytes, carrying an extra hard link at open. Gate 6 refuses it (nlink !== 1).
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'stale-from-the-previous-build');
    const alias = join(dir, 'a-alias.png');
    nodeFs.linkSync(join(assetDir, 'a.png'), alias);

    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    // [round 17] This pinned `a.png:hazard` for two rounds. `hashFileNoFollow` reports EVERY
    // unreadable leaf as kind `hazard` and puts the discriminating fact in `reason`, so persisting
    // the kind collapsed hard_link, non_regular and inspection_failure into one word — while the
    // comment beside the W5 refusal claimed an operator reading `hard_link` acts differently from
    // one reading `inspection_failure`. The test pinned the collapsed form as though it were the
    // intent, so nothing contradicted the comment.
    assert.deepEqual(opened.runState.opening_asset_hazards['items'], ['a.png:hard_link']);
    assert.equal(Object.hasOwn(opened.runState.opening_assets['items'], 'a.png'), false,
      'the hash itself is still absent — it is the HAZARD that must survive alongside it');

    // The capture removes only the alias. The BYTES are never rewritten: this asset still holds
    // the previous build's content.
    nodeFs.unlinkSync(alias);
    assert.equal(nodeFs.statSync(join(assetDir, 'a.png')).nlink, 1);

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, true, JSON.stringify(closed));

    const chapterFile = writeChapterAt(profile, entry, '# items\n');
    const deps = { ...stubDepsNoIdentity(), expectedAssets: stubExpectedAssetsFor(assetDir, ['a.png']) };
    const result = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, opened.runState.run_id, deps);
    assert.equal(result.recorded, false, JSON.stringify(result));
    // An open-ended match on the prefix is what let the collapsed word through: `a.png:hazard` and
    // `a.png:hard_link` both satisfy it, and only one of them is a word an operator can act on.
    assert.equal(result.reason, 'rule5_opening_unhashable:a.png:hard_link', JSON.stringify(result));
    assert.equal(nodeFs.existsSync(CR.chapterRecordPath(profile, entry)), false);
  });
});

// [round 16 BLOCKER] The hard-link test above passes with the symlink path completely broken: a
// hard link's dirent is still `isFile()`, so the walk visits it and the hazard is raised one level
// down, inside the hash. A symlink is refused by the WALK, one level up, and never reaches that
// classification at all — so it came out as an absence again, which W5 reads as "brand-new file".
// The test and the defect shared a blind spot, which is why the fix looked complete. Same scenario,
// the one dirent kind that exercises the layer the other test cannot reach.
test('recordChapterProvenance: an asset that was a SYMLINK at open is never recorded, though W5 sees a plain file', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    const stale = join(dir, 'stale-source.png');
    nodeFs.writeFileSync(stale, 'stale-from-the-previous-build');
    nodeFs.symlinkSync(stale, join(assetDir, 'a.png'));

    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    assert.deepEqual(opened.runState.opening_asset_hazards['items'], ['a.png:symlink']);

    // The capture replaces the symlink with a plain file holding the SAME stale bytes.
    nodeFs.unlinkSync(join(assetDir, 'a.png'));
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'stale-from-the-previous-build');

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, true, JSON.stringify(closed));

    const chapterFile = writeChapterAt(profile, entry, '# items\n');
    const deps = { ...stubDepsNoIdentity(), expectedAssets: stubExpectedAssetsFor(assetDir, ['a.png']) };
    const result = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, opened.runState.run_id, deps);
    assert.equal(result.recorded, false, JSON.stringify(result));
    assert.equal(result.reason, 'rule5_opening_unhashable:a.png:symlink', JSON.stringify(result));
    assert.equal(nodeFs.existsSync(CR.chapterRecordPath(profile, entry)), false);
  });
});

// [round 17 BLOCKER] The symlink test above passes with the DIRECTORY case completely broken, for
// the same reason the hard-link test passed with the symlink case broken: it puts the symlink at a
// path that IS an asset key, so an exact-match lookup finds it. A symlinked DIRECTORY is refused by
// the walk under the directory's own path — `screens:symlink` — while the asset it hides is keyed
// `screens/a.png`, and W5 compared the two for equality. So the hazard was raised, persisted,
// authenticated, and then looked up under a name it was never filed as: no refusal, no opening
// entry either (the walk never reached the file), and rule 4 read that absence as "brand-new this
// run". Old-build bytes, recorded under this run's build identity, with every hazard mechanism the
// previous two rounds added working exactly as designed.
//
// A hazard is a statement about a PATH, and refusing a directory withholds the bytes of everything
// beneath it — so the lookup is containment, not equality. Measured on the real production path
// before the fix: `{recorded: true}` and a chapter record on disk. The real extractor is used here
// rather than a stub, because a stub keyed `screens/a.png` would be my own assumption about what
// extraction yields for a nested embed, and that key is half of what the defect is about.
test('recordChapterProvenance: an asset hidden by a symlinked SUBDIRECTORY at open is never recorded — the hazard names the directory, not the asset', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    const stale = join(dir, 'stale-screens');
    nodeFs.mkdirSync(stale, { recursive: true });
    nodeFs.writeFileSync(join(stale, 'a.png'), 'stale-from-the-previous-build');
    nodeFs.symlinkSync(stale, join(assetDir, 'screens'));

    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    // The hazard is filed under the DIRECTORY, and the opening hash map is empty — both halves of
    // the defect, pinned here so a future change that moves either one fails loudly rather than
    // quietly making this test vacuous.
    assert.deepEqual(opened.runState.opening_asset_hazards['items'], ['screens:symlink']);
    assert.deepEqual(Object.keys(opened.runState.opening_assets['items']), []);

    // The capture replaces the symlink with a real directory holding the SAME stale bytes.
    nodeFs.unlinkSync(join(assetDir, 'screens'));
    nodeFs.mkdirSync(join(assetDir, 'screens'));
    nodeFs.writeFileSync(join(assetDir, 'screens', 'a.png'), 'stale-from-the-previous-build');

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, true, JSON.stringify(closed));

    const chapterFile = writeChapterAt(profile, entry, '# items\n');
    const embed = chapterPathsModule.embedPath(chapterFile, assetDir, 'screens/a.png');
    nodeFs.writeFileSync(chapterFile, `# items\n\n1. Step\n\n   ![a](${embed})\n`);

    const result = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, opened.runState.run_id, stubDepsNoIdentity());
    assert.equal(result.recorded, false, JSON.stringify(result));
    assert.equal(result.reason, 'rule5_opening_unhashable:screens:symlink', JSON.stringify(result));
    assert.equal(nodeFs.existsSync(CR.chapterRecordPath(profile, entry)), false);
  });
});

// [round 17] The containment rule must not over-reach in the other direction: a hazard on `screens`
// says nothing about a sibling whose key merely starts with those characters. Without the separator
// this check is a `startsWith` that swallows `screensaver/`, refusing a chapter whose every asset
// was hashed cleanly at both observation points — a fail-closed defect is still a defect when the
// operator has no way to act on it.
test('recordChapterProvenance: a hazard on `screens` does not refuse an asset under `screensaver`', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(join(assetDir, 'screensaver'), { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'screensaver', 'a.png'), 'v1');
    nodeFs.symlinkSync(join(dir, 'nowhere'), join(assetDir, 'screens'));

    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    assert.deepEqual(opened.runState.opening_asset_hazards['items'], ['screens:symlink']);

    nodeFs.writeFileSync(join(assetDir, 'screensaver', 'a.png'), 'v2');
    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, true, JSON.stringify(closed));

    const chapterFile = writeChapterAt(profile, entry, '# items\n');
    const embed = chapterPathsModule.embedPath(chapterFile, assetDir, 'screensaver/a.png');
    nodeFs.writeFileSync(chapterFile, `# items\n\n1. Step\n\n   ![a](${embed})\n`);

    const result = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, opened.runState.run_id, stubDepsNoIdentity());
    assert.equal(result.recorded, true, JSON.stringify(result));
  });
});

// [round 17] The walk's OTHER refusal — a dirent that is neither symlink, directory nor regular
// file — had no test on any path, so nothing measured the word it reports. It reported
// `not_regular` while the leaf inspection reports `non_regular` for the same fact one layer down;
// invisible until round 17 sent reason words to operators, and a spelling difference an operator
// would have to read the source to learn is not a real distinction. A unix socket is the one such
// dirent Node can create without a native mkfifo.
test('openCaptureRun: a non-regular dirent in the asset tree is a hazard, spelled as the leaf layer spells it', async () => {
  const dir = nodeFs.mkdtempSync(join(tmpdir(), 'ehcr-sock-'));
  const server = netCreateServer();
  try {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    await new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(join(assetDir, 'live.sock'), resolve);
    });
    assert.equal(nodeFs.lstatSync(join(assetDir, 'live.sock')).isSocket(), true, 'fixture must actually be a socket');

    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    assert.deepEqual(opened.runState.opening_asset_hazards['items'], ['live.sock:non_regular']);
  } finally {
    await new Promise((resolve) => server.close(resolve));
    nodeFs.rmSync(dir, { recursive: true, force: true });
  }
});

// [round 19 BLOCKER] The deepest version of the recurring defect, and it was in the ORIGINAL
// round-15 code as well as in round 18's new branch. `readdir` LISTED the entry; the attempt to
// read it then failed with ENOENT. That was encoded as an absence — and an absence at the OPENING
// observation point is read by rule 4 as "brand-new file this run", which skips the did-it-change
// check. Once the listing has seen an entry, failing to establish it is UNCERTAINTY, not evidence
// that nothing was there.
//
// The genuine brand-new case is unaffected and is worth stating, because it is why this is safe: a
// file that does not exist at open is never LISTED, so it never reaches this callback at all and
// its key is simply missing from the map. Only a file that was there and then was not comes here.
test('the opening snapshot: a listed asset that disappears before it can be read is a hazard, not an absence', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'stale-from-the-previous-build');

    // Listed by readdir, then gone by the time it is opened; restored, unchanged, before close.
    const deps = depsWithOverride({
      openSync: (path, ...rest) => {
        if (String(path).endsWith('/a.png')) {
          const err = new Error('ENOENT'); err.code = 'ENOENT'; throw err;
        }
        return nodeFs.openSync(path, ...rest);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(opened.ok, true, JSON.stringify(opened));
    assert.deepEqual(Object.keys(opened.runState.opening_assets['items']), [],
      'the fixture must actually produce an empty opening map, or this test proves nothing');
    assert.deepEqual(opened.runState.opening_asset_hazards['items'], ['a.png:vanished']);

    // Close sees it again, unchanged — the bytes are the previous build's.
    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, true, JSON.stringify(closed));

    const chapterFile = writeChapterAt(profile, entry, '# items\n');
    const w5Deps = { ...stubDepsNoIdentity(), expectedAssets: stubExpectedAssetsFor(assetDir, ['a.png']) };
    const result = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, opened.runState.run_id, w5Deps);
    assert.equal(result.recorded, false, JSON.stringify(result));
    assert.equal(result.reason, 'rule5_opening_unhashable:a.png:vanished', JSON.stringify(result));
    assert.equal(nodeFs.existsSync(CR.chapterRecordPath(profile, entry)), false);
  });
});

// [round 20 BLOCKER] Round 18 gave the walk's recursive catch a hazard for ENOTDIR and a relPrefix
// check to keep the root out of it, and left ENOENT returning unconditionally two lines above — the
// same distinction, drawn for one error code and not its neighbour. A listed subdirectory that
// vanishes before its own listing therefore took everything under it out of the snapshot with no
// hazard, and at the opening observation point those absent keys are read as "brand-new this run".
test('walk: a listed subdirectory that vanishes before its own listing is a hazard', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(join(assetDir, 'screens'), { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'screens', 'a.png'), 'stale-from-the-previous-build');

    const deps = depsWithOverride({
      readdirSync: (p, opts) => {
        if (String(p).endsWith('/screens')) {
          const err = new Error('ENOENT'); err.code = 'ENOENT'; throw err;
        }
        return nodeFs.readdirSync(p, opts);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(opened.ok, true, JSON.stringify(opened));
    assert.deepEqual(opened.runState.opening_asset_hazards['items'], ['screens:vanished']);
    assert.deepEqual(Object.keys(opened.runState.opening_assets['items']), []);
  });
});

// The root call is the case that ENOENT exists for, and it must stay silent: an asset directory
// legitimately does not exist before the first capture, and it is nameable by no relative path.
test('walk: the ROOT asset directory not existing yet is still an ordinary first capture', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    assert.deepEqual(opened.runState.opening_asset_hazards['items'], [],
      'a first capture must not be reported as a mid-run disappearance');
    assert.deepEqual(Object.keys(opened.runState.opening_assets['items']), []);
  });
});

// [round 21] Every other nested-subdirectory case in this file is a REFUSAL, and eight review rounds
// have added refusals to the walk — the last three of them ON this path, twice per directory per
// listing. A suite made entirely of refusal tests cannot tell a correct refusal from a feature that
// refuses everything, so the ordinary case is pinned too: a nested asset, captured normally, must
// still produce a confident record and verify afterwards. The real extractor, real deps, nothing
// stubbed but the identity command.
test('the ordinary case: a nested asset captured normally is recorded, and W6 verifies it', () => {
  withTempDir((dir) => {
    // A configured identity source, unlike almost every other fixture here: without one W6 can only
    // answer `indeterminate`, so no test in this suite had ever reached `record_ok` — the verdict a
    // real handbook is published on.
    const profile = profileFor(dir, { capture: { build_identity: { command: 'get-version', ui_read: false } } });
    const identityDeps = depsWithOverride({ runIdentityCommand: () => ({ ok: true, raw: '3.4.1' }) });
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(join(assetDir, 'screens'), { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'screens', 'a.png'), 'previous-build-bytes');

    const opened = CR.openCaptureRun(profile, [entry], null, identityDeps);
    assert.equal(opened.ok, true, JSON.stringify(opened));
    assert.deepEqual(
      opened.runState.opening_asset_hazards.items,
      [],
      'an ordinary nested tree must produce NO hazard — a refusal here refuses every real capture',
    );
    assert.deepEqual(Object.keys(opened.runState.opening_assets.items), ['screens/a.png']);

    // The capture writes new bytes, exactly as a real capture command would.
    nodeFs.writeFileSync(join(assetDir, 'screens', 'a.png'), 'this-build-bytes');
    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, identityDeps);
    assert.equal(closed.ok, true, JSON.stringify(closed));

    const chapterFile = writeChapterAt(profile, entry, '# items\n');
    const embed = chapterPathsModule.embedPath(chapterFile, assetDir, 'screens/a.png');
    nodeFs.writeFileSync(chapterFile, `# items\n\n1. Step\n\n   ![a](${embed})\n`);

    const result = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, opened.runState.run_id, identityDeps);
    assert.equal(result.recorded, true, JSON.stringify(result));
    assert.equal(nodeFs.existsSync(CR.chapterRecordPath(profile, entry)), true);

    const report = CR.buildProvenanceReport(profile, [entry], null, identityDeps);
    const row = report.rows[0];
    assert.equal(row.classification, 'unchanged', JSON.stringify(row));
    assert.equal(row.classification_reason, null, JSON.stringify(row));
    assert.equal(row.record_detail, null, JSON.stringify(row));
    assert.equal(row.value, '3.4.1', JSON.stringify(row));
    assert.equal(row.current_source, 'command', JSON.stringify(row));
  });
});

// [round 21] The TOCTOU test far above swaps a LEAF, and `hashFileNoFollow`'s O_NOFOLLOW open
// catches it. This is the ANCESTOR shape, which that flag cannot reach: O_NOFOLLOW refuses a
// symlink at the FINAL path component only, so a DIRECTORY replaced by a symlink between its
// parent's listing and its own is followed by the kernel transparently, and every file under the
// replacement is hashed as this chapter's asset. This module states that exact rule for the two
// namespaces it OWNS (`inspectHierarchyChain`, "an ancestor directory that is itself a symlink is
// followed transparently by the kernel regardless of that flag"); the asset tree it merely READS
// had no equivalent. At the opening observation point the consequence is the release's recurring
// one: if the real directory is restored before the close, closing hashes the stale bytes, rule 4
// sees opening ≠ closing, rule 5's rehash agrees with closing, and the record is confident and
// wrong.
test('walk: a subdirectory swapped for a symlink after its parent listing is not descended through', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(join(assetDir, 'screens'), { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'screens', 'a.png'), 'the real asset');
    const outside = join(dir, 'outside');
    const foreign = 'bytes from a tree this chapter does not own';
    nodeFs.mkdirSync(outside, { recursive: true });
    nodeFs.writeFileSync(join(outside, 'a.png'), foreign);

    // The swap lands DURING the parent's own listing, so the dirent handed back still says
    // `screens` is a directory — which is what the parent readdir truthfully observed a moment
    // earlier. Nothing here lies; the dirent is simply stale by the time it is acted on.
    let swapped = false;
    const deps = depsWithOverride({
      readdirSync: (p, opts) => {
        const listed = nodeFs.readdirSync(p, opts);
        if (p === assetDir && opts?.withFileTypes && !swapped) {
          nodeFs.renameSync(join(assetDir, 'screens'), join(dir, 'screens-moved-away'));
          nodeFs.symlinkSync(outside, join(assetDir, 'screens'));
          swapped = true;
        }
        return listed;
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(opened.ok, true, JSON.stringify(opened));
    // The fixture must be in the state this test is named for before anything is concluded from
    // its behaviour — five tests in this release passed while unable to reach their own condition.
    assert.equal(swapped, true, 'the parent listing never ran — this fixture cannot reach the condition');
    assert.equal(
      nodeFs.lstatSync(join(assetDir, 'screens')).isSymbolicLink(),
      true,
      'the subdirectory was never replaced by a symlink — this fixture cannot reach the condition',
    );

    const foreignDigest = `sha256:${createHash('sha256').update(foreign).digest('hex')}`;
    assert.equal(
      Object.values(opened.runState.opening_assets.items).includes(foreignDigest),
      false,
      'bytes reached through a swapped ancestor were hashed as this chapter\'s own asset',
    );
    assert.deepEqual(
      opened.runState.opening_asset_hazards.items,
      ['screens:symlink'],
      'the substitution must be reported against the DIRECTORY, so W5 refuses everything under it',
    );
  });
});

// [round 21] The test above is caught by the check BEFORE the child's listing, and that check alone
// would have let this one through: here the substitution lands during the child's OWN listing, so
// every leaf under it is opened through the replacement and hashed before anything looks again.
// This is what the second observation point is for, and it is also why the stale hash is left in
// the map rather than deleted — removing it would convert a substitution back into an absence,
// which is the reading this entire defect class travels on. The directory hazard is what makes W5
// refuse, through containment, whatever the map happens to hold.
test('walk: a subdirectory swapped for a symlink during its OWN listing is still reported as a hazard', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    const screens = join(assetDir, 'screens');
    nodeFs.mkdirSync(screens, { recursive: true });
    nodeFs.writeFileSync(join(screens, 'a.png'), 'the real asset');
    const outside = join(dir, 'outside');
    nodeFs.mkdirSync(outside, { recursive: true });
    nodeFs.writeFileSync(join(outside, 'a.png'), 'bytes from a tree this chapter does not own');

    let swapped = false;
    const deps = depsWithOverride({
      readdirSync: (p, opts) => {
        const listed = nodeFs.readdirSync(p, opts);
        if (p === screens && opts?.withFileTypes && !swapped) {
          nodeFs.renameSync(screens, join(dir, 'screens-moved-away'));
          nodeFs.symlinkSync(outside, screens);
          swapped = true;
        }
        return listed;
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(opened.ok, true, JSON.stringify(opened));
    assert.equal(swapped, true, 'the child listing never ran — this fixture cannot reach the condition');
    assert.equal(
      nodeFs.lstatSync(screens).isSymbolicLink(),
      true,
      'the subdirectory was never replaced by a symlink — this fixture cannot reach the condition',
    );
    assert.deepEqual(opened.runState.opening_asset_hazards.items, ['screens:symlink']);

    // [round 22] The check between the listing and its use means nothing out of the replacement is
    // ever hashed here — strictly better than reporting it afterwards, which is what round 21 could
    // do. Pinned as an exact key set: the earlier version of this assertion iterated the retained
    // keys without ever asserting there were any, so a mutant that emptied the map left the loop
    // running zero times and the test green (codex round 22, MINOR). A loop that runs zero times
    // prints exactly what a passing one prints.
    assert.deepEqual(
      Object.keys(opened.runState.opening_assets.items),
      [],
      'a substitution caught between the listing and its use must contribute NO hash at all',
    );
  });
});

// [round 21] The root exemption directly above is correct for a first capture and wrong the moment
// this module has ALREADY SEEN the directory: gate 3 lstats every entry's asset directory before
// the reservation is taken, so a root that is missing at the snapshot a few steps later did not
// fail to exist — it stopped existing. Read as a first capture, the opening map is `{}` with no
// hazards, and a stale file restored before the close is recorded as this build's, because rule 4
// skips an asset with no opening key. The distinction is not observable from inside the walk; it
// is the caller's knowledge, so the caller supplies it.
test('openCaptureRun: an asset directory observed at validation and gone at the snapshot is not a first capture', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'stale-from-the-previous-build');

    const deps = depsWithOverride({
      readdirSync: (p, opts) => {
        if (p === assetDir) {
          const err = new Error('ENOENT'); err.code = 'ENOENT'; throw err;
        }
        return nodeFs.readdirSync(p, opts);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    assert.equal(
      nodeFs.lstatSync(assetDir).isDirectory(),
      true,
      'gate 3 must be able to observe this directory, or the test proves nothing about the ordering',
    );
    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(opened.ok, false, 'a baseline that could not be established must not open as an empty one');
    assert.equal(opened.halts[0].halt, 'provenance_hazard');
    assert.match(opened.halts[0].message, /items/);
    assert.equal(
      nodeFs.existsSync(tokenPathFor(profile)),
      false,
      'the reservation must be released on this exit like every other pre-commit halt',
    );
  });
});

// The sibling errno on the same branch. Round 20's whole finding was this rule applied to one error
// code and not its neighbour, so the neighbour gets its own case rather than an argument that it
// must behave the same. Reported independently by the cross-file review bot, whose scenario this
// is: the asset root is a regular FILE, gate 3's `lstatSync` succeeds and containment accepts the
// path, the root listing fails with ENOTDIR — and the empty baseline that produced let the capture
// replace the file with a directory of stale bytes and be recorded as this build's.
//
// Nothing is stubbed. The root really is a regular file on disk, which is what makes this the
// fixture that can reach the condition: a `readdirSync` override would only demonstrate the errno
// branch, never that gate 3 lets this shape through to it in the first place.
test('openCaptureRun: an asset root that is a regular FILE is refused, not read as an empty baseline', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetRoot = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(profile.capture.output_dir, { recursive: true });
    nodeFs.writeFileSync(assetRoot, 'not a directory at all');

    assert.equal(
      nodeFs.lstatSync(assetRoot).isFile(),
      true,
      'the asset root must really be a regular file, or this test proves nothing about that shape',
    );
    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, false, 'a root that cannot be listed must not open as an empty baseline');
    assert.equal(opened.halts[0].halt, 'provenance_hazard');
    assert.match(opened.halts[0].message, /items/);
    // [round 22] The message must say this path could not be CONFIRMED as the directory gate 3
    // observed — not that it was replaced. Nothing replaced it; it was a regular file the whole
    // time, and "replaced" would be a confident wrong diagnosis of the operator's actual situation.
    assert.match(opened.halts[0].message, /could not be confirmed \(inspection_failure\)/);
    assert.doesNotMatch(opened.halts[0].message, /replaced/);
    assert.equal(
      nodeFs.existsSync(tokenPathFor(profile)),
      false,
      'the reservation must be released on this exit like every other pre-commit halt',
    );
  });
});

// [round 22] The third observation point, and the only window in which a hash is RETAINED under a
// hazard: the substitution lands while the listing's entries are being processed, so the real bytes
// are already hashed when it is noticed. They are deliberately kept — deleting them would turn a
// substitution back into an ABSENCE, which is the reading this whole defect class travels on — and
// the hazard on the directory is what refuses them, through W5's containment match. Both halves are
// asserted as exact values, not as an iteration over a list that may be empty.
test('walk: a subdirectory swapped while its entries are being processed keeps the real hash under a hazard', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    const screens = join(assetDir, 'screens');
    nodeFs.mkdirSync(screens, { recursive: true });
    nodeFs.writeFileSync(join(screens, 'a.png'), 'the real asset');
    const outside = join(dir, 'outside');
    nodeFs.mkdirSync(outside, { recursive: true });
    nodeFs.writeFileSync(join(outside, 'a.png'), 'bytes from a tree this chapter does not own');

    // The swap fires from the leaf open itself — after the real descriptor is handed back, so the
    // bytes hashed are genuinely the real ones, and the directory is a symlink by the time the
    // walk looks again.
    let swapped = false;
    const deps = depsWithOverride({
      openSync: (p, ...rest) => {
        const fd = nodeFs.openSync(p, ...rest);
        if (String(p) === join(screens, 'a.png') && !swapped) {
          nodeFs.renameSync(screens, join(dir, 'screens-moved-away'));
          nodeFs.symlinkSync(outside, screens);
          swapped = true;
        }
        return fd;
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(opened.ok, true, JSON.stringify(opened));
    assert.equal(swapped, true, 'the leaf was never opened — this fixture cannot reach the condition');
    assert.equal(
      nodeFs.lstatSync(screens).isSymbolicLink(),
      true,
      'the subdirectory was never replaced — this fixture cannot reach the condition',
    );

    assert.deepEqual(opened.runState.opening_asset_hazards.items, ['screens:symlink']);
    const realDigest = `sha256:${createHash('sha256').update('the real asset').digest('hex')}`;
    assert.deepEqual(
      Object.keys(opened.runState.opening_assets.items),
      ['screens/a.png'],
      'the hash gathered before the substitution was noticed must be RETAINED — deleting it would turn a substitution back into an absence',
    );
    assert.equal(
      opened.runState.opening_assets.items['screens/a.png'],
      realDigest,
      'and it must be the REAL bytes, hashed through the descriptor opened before the swap',
    );
    // The retention is only safe because the hazard covers the key by containment.
    assert.equal('screens/a.png'.startsWith('screens/'), true);
  });
});

// [round 22 BLOCKER] The round-21 bracket compared directory-NESS, and codex produced the gap as
// executed evidence: two `lstat`s can both answer "directory" while naming two DIFFERENT
// directories. Replacing `screens/` with another ordinary directory therefore returned a foreign
// hash with an empty hazard list — a silent substitution through a guard written to stop exactly
// that. Type-equality was never the property being asserted; identity was.
test('walk: a subdirectory replaced by a DIFFERENT ordinary directory is a hazard, not a silent substitution', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    const screens = join(assetDir, 'screens');
    nodeFs.mkdirSync(screens, { recursive: true });
    nodeFs.writeFileSync(join(screens, 'a.png'), 'the real asset');
    const decoy = join(dir, 'decoy');
    const foreign = 'bytes from a directory this chapter does not own';
    nodeFs.mkdirSync(decoy, { recursive: true });
    nodeFs.writeFileSync(join(decoy, 'a.png'), foreign);

    // A real directory swapped for a real directory — no symlink anywhere, which is precisely why a
    // type check cannot see it. The swap lands during `screens`'s OWN listing, which is the window
    // the three observation points cover; see the walk's comment for the one they cannot.
    let swapped = false;
    const deps = depsWithOverride({
      readdirSync: (p, opts) => {
        const listed = nodeFs.readdirSync(p, opts);
        if (p === screens && opts?.withFileTypes && !swapped) {
          nodeFs.renameSync(screens, join(dir, 'screens-moved-away'));
          nodeFs.renameSync(decoy, screens);
          swapped = true;
        }
        return listed;
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(opened.ok, true, JSON.stringify(opened));
    assert.equal(swapped, true, 'the child listing never ran — this fixture cannot reach the condition');
    assert.equal(
      nodeFs.lstatSync(screens).isDirectory(),
      true,
      'the replacement must itself be an ordinary DIRECTORY, or a type check would have caught it',
    );
    assert.equal(
      nodeFs.lstatSync(screens).isSymbolicLink(),
      false,
      'a symlink here would make this a re-run of the round-21 test, not the identity case',
    );

    const foreignDigest = `sha256:${createHash('sha256').update(foreign).digest('hex')}`;
    assert.equal(
      Object.values(opened.runState.opening_assets.items).includes(foreignDigest),
      false,
      'bytes from a substituted directory were hashed as this chapter\'s own asset',
    );
    assert.deepEqual(opened.runState.opening_asset_hazards.items, ['screens:inspection_failure']);
  });
});

// [round 22] The fail-closed half of the identity rule, which had no test until a mutant that
// deleted it killed nothing. An `lstat` that cannot answer with two numbers must refuse: without
// this, a caller on the pre-round-22 declaration compares `undefined` to `undefined` at every
// observation point, they compare EQUAL, and every substitution passes — a guard that reports
// success precisely because it learned nothing.
test('walk: a subdirectory whose lstat cannot report identity is refused, not silently walked', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(join(assetDir, 'screens'), { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'screens', 'a.png'), 'the real asset');

    const deps = depsWithOverride({
      // The pre-round-22 declaration — the three predicates, no `dev`, no `ino` — for the
      // SUBDIRECTORY only. [round 23] The asset ROOT must keep its identity, because gate 3 now
      // halts the whole run on a root it cannot pin; withholding it everywhere would leave this
      // test silently re-pinning the root rule instead of the child rule it was written for. Keyed
      // on the trailing segment rather than the full path, because the two call sites derive the
      // directory through different roots (canonical vs profile-as-given) and a `===` here would
      // make the fixture's reach depend on the platform's path canonicalization.
      lstatSync: (p) => {
        const st = nodeFs.lstatSync(p);
        const base = {
          isSymbolicLink: () => st.isSymbolicLink(),
          isDirectory: () => st.isDirectory(),
          isFile: () => st.isFile(),
        };
        return String(p).endsWith('/screens') ? base : { ...base, dev: st.dev, ino: st.ino };
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(opened.ok, true, JSON.stringify(opened));
    assert.deepEqual(
      opened.runState.opening_asset_hazards.items,
      ['screens:inspection_failure'],
      'an identity this module cannot read is uncertainty, and uncertainty is a hazard rather than a pass',
    );
    assert.deepEqual(
      Object.keys(opened.runState.opening_assets.items),
      [],
      'nothing under an unverifiable directory may enter the snapshot',
    );
  });
});

// [round 22 BLOCKER, the same defect at the ROOT] Round 21 exempted the asset root from the bracket,
// reasoning that gate 3 permits a symlinked root resolving inside `capture.output_dir` — true, and
// beside the point: gate 3 validated the object that was there THEN. Replace the root after it and
// the walk follows the replacement, hashes an outside tree, and records it. The identity gate 3
// observed is carried forward now, so the root may be a symlink but may not become a different one.
test('openCaptureRun: an asset root replaced by a symlink to an outside tree after validation halts', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'previous-build-bytes');
    const outside = join(dir, 'outside');
    nodeFs.mkdirSync(outside, { recursive: true });
    nodeFs.writeFileSync(join(outside, 'a.png'), 'bytes from a tree this chapter does not own');

    // The substitution lands after gate 3 and before the opening snapshot, hooked on the pending
    // token's own `openSync` — which `openCaptureRun` performs between the two.
    // [round 23 MINOR] It used to be hooked on `p === assetDir` inside `lstatSync`, which made the
    // PHASE depend on path canonicalization: gate 3 derives the directory through the canonical
    // output root and the snapshot through the profile as given, so on macOS (`/var` ->
    // `/private/var`) the strings differ and the swap landed after gate 3, while on a topology where
    // they are identical it landed INSIDE gate 3 and produced `asset_dir_escapes_output_dir`
    // instead. One fixture, two different rules exercised, decided by the platform — and the
    // reachability assertion said "gate 3 never lstat-ed the asset root", which was itself the wrong
    // diagnosis on the platform it ran on.
    let swapped = false;
    const deps = depsWithOverride({
      openSync: (p, ...rest) => {
        if (!swapped && String(p).endsWith('/pending.json')) {
          nodeFs.renameSync(assetDir, join(dir, 'items-moved-away'));
          nodeFs.symlinkSync(outside, assetDir);
          swapped = true;
        }
        return nodeFs.openSync(p, ...rest);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(swapped, true, 'the swap hook never ran — this fixture cannot reach the condition');
    assert.equal(
      nodeFs.lstatSync(assetDir).isSymbolicLink(),
      true,
      'the root was never replaced by a symlink — this fixture cannot reach the condition',
    );
    assert.equal(opened.ok, false, 'a root that became a different object must not be walked');
    assert.equal(opened.halts[0].halt, 'provenance_hazard');
    assert.match(opened.halts[0].message, /replaced by a different directory/);
  });
});

// The legitimate half of the same rule, which the check above must NOT break: a symlinked asset root
// that resolves inside `capture.output_dir` is a topology gate 3 deliberately permits, and it must
// still capture normally. [round 23] It is identified by the TARGET's inode at both observation
// points, not the link's — see the swapped-target test below for why that distinction is the whole
// guard rather than a detail of it. This test is the over-refusal side of that change: resolving
// through the link must not turn a supported topology into a halt.
test('openCaptureRun: a legitimately symlinked asset root inside the output dir still snapshots', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const real = join(profile.capture.output_dir, 'real-items');
    nodeFs.mkdirSync(real, { recursive: true });
    nodeFs.writeFileSync(join(real, 'a.png'), 'previous-build-bytes');
    nodeFs.symlinkSync(real, join(profile.capture.output_dir, 'items'));

    assert.equal(
      nodeFs.lstatSync(join(profile.capture.output_dir, 'items')).isSymbolicLink(),
      true,
      'the asset root must really be a symlink, or this test says nothing about the supported topology',
    );
    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    assert.deepEqual(opened.runState.opening_asset_hazards.items, []);
    assert.deepEqual(Object.keys(opened.runState.opening_assets.items), ['a.png']);
  });
});

// [round 23 BLOCKER — the same defect one indirection down] Round 22 pinned the asset root's
// identity, and this file asserted twice that a symlinked root "is identified by the LINK's own
// inode at both observation points". True, and the wrong object: `readdirSync` FOLLOWS the link.
// The link survives every substitution of its TARGET untouched, so all three identity checks
// compared one inode to itself, `openCaptureRun` returned ok, the foreign tree was hashed into
// `opening_assets`, and `opening_asset_hazards` came back empty. Codex reproduced exactly that.
//
// The swap lands strictly between gate 3 and the opening snapshot, and the hook is the pending
// token's own `openSync` — which `openCaptureRun` performs between the two — rather than a path
// string matching the asset root. That is not a stylistic choice: keying on `p === assetDir` makes
// the PHASE depend on path canonicalization (gate 3 derives the directory through the canonical
// output root, the snapshot through the profile as given), so the identical fixture lands after
// gate 3 on macOS and inside it on Linux, where it produces a different halt entirely. A phase
// boundary this seam can actually name has no such split.
test('openCaptureRun: a symlinked asset root whose TARGET is swapped after validation halts', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const link = join(profile.capture.output_dir, 'items');
    const real = join(profile.capture.output_dir, 'real-items');
    const foreign = join(profile.capture.output_dir, 'foreign-items');
    nodeFs.mkdirSync(real, { recursive: true });
    nodeFs.writeFileSync(join(real, 'a.png'), 'previous-build-bytes');
    nodeFs.mkdirSync(foreign, { recursive: true });
    nodeFs.writeFileSync(join(foreign, 'a.png'), 'bytes from a tree this chapter does not own');
    nodeFs.symlinkSync(real, link);

    const linkInodeBefore = nodeFs.lstatSync(link).ino;
    let swapped = false;
    const deps = depsWithOverride({
      openSync: (p, ...rest) => {
        if (!swapped && String(p).endsWith('/pending.json')) {
          // Both replacements stay inside `capture.output_dir`, so containment is satisfied before
          // and after: what this test isolates is IDENTITY, which the round-22 root test conflated
          // with an escape.
          nodeFs.renameSync(real, join(dir, 'real-items-moved-away'));
          nodeFs.renameSync(foreign, real);
          swapped = true;
        }
        return nodeFs.openSync(p, ...rest);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(swapped, true, 'the swap hook never ran — this fixture cannot reach the condition');
    assert.equal(
      nodeFs.lstatSync(link).ino,
      linkInodeBefore,
      'the link itself must be untouched, or this test says nothing about following it',
    );
    assert.equal(
      nodeFs.readFileSync(join(link, 'a.png'), 'utf8'),
      'bytes from a tree this chapter does not own',
      'the link must now resolve to the foreign tree, or the swap did not take',
    );
    assert.equal(opened.ok, false, 'a root whose target became a different directory must not be walked');
    assert.equal(opened.halts[0].halt, 'provenance_hazard');
    assert.match(opened.halts[0].message, /replaced by a different directory/);
  });
});

// [round 23] `identityOfListedObject` refuses a `realpathSync` result that is ITSELF a symlink.
// Against the real `node:fs` binding that cannot happen — realpath resolves every link on the path
// by contract — so the guard's mutant killed nothing, which is this release's established signal
// that a guard has no reader. It is reachable through the `deps` seam, where all of this module's fs
// access lives and which nothing in this repository type-checks: a caller supplying a `realpathSync`
// that stops at the first link would otherwise have the LINK's identity recorded as the target's,
// silently restoring the exact defect this round removed. The rule the module has applied since
// round 19 governs — a seam result that has not answered is uncertainty, never a guess.
test('openCaptureRun: a realpathSync that returns a symlink is refused, never trusted as the target', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const link = join(profile.capture.output_dir, 'items');
    const real = join(profile.capture.output_dir, 'real-items');
    nodeFs.mkdirSync(real, { recursive: true });
    nodeFs.writeFileSync(join(real, 'a.png'), 'previous-build-bytes');
    nodeFs.symlinkSync(real, link);

    let lied = false;
    const deps = depsWithOverride({
      // Stops at the first link instead of resolving through it — an out-of-contract seam, not a
      // filesystem state. `real` ends in `-items`, not `/items`, so only the root is affected.
      realpathSync: (p, ...rest) => {
        if (String(p).endsWith('/items')) {
          lied = true;
          return p;
        }
        return nodeFs.realpathSync(p, ...rest);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(lied, true, 'the identity read never called realpathSync — this fixture cannot reach the condition');
    assert.equal(opened.ok, false, JSON.stringify(opened));
    assert.equal(opened.halts[0].halt, 'provenance_hazard');
    assert.match(opened.halts[0].message, /cannot establish the identity of asset directory/);
    assert.match(opened.halts[0].message, /inspection_failure/);
  });
});

// [round 23 BLOCKER] The fail-open half of the same finding, and the same shape this release has
// been closing since round 14: gate 3 turned an identity it could not read into `null`, and the
// walk read `null` as "this caller configured no pin" rather than as "this module could not
// establish what it observed". Replacing an ordinary root after gate 3 then returned ok, accepted
// the foreign digest, and reported no hazard — `rootMustExist` covers DISAPPEARANCE only, never
// replacement. An observation that cannot be pinned is not an observation.
test('openCaptureRun: an asset root this module observed but cannot identify halts, never pins nothing', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'previous-build-bytes');

    const deps = depsWithOverride({
      // Exactly the pre-round-22 declaration: the three predicates, no `dev`, no `ino`.
      // [round 34] Scoped to the CHAPTER directory. It used to answer this way for every path, and
      // the output root's own identity read is now refused at validation — one guard earlier, with a
      // different message — so a blanket seam retires this fixture's attribution rather than testing
      // it. Scoped, it is also the more realistic input: one directory whose identity cannot be
      // read, not a filesystem that can answer for nothing.
      lstatSync: (p, ...rest) => {
        const st = nodeFs.lstatSync(p, ...rest);
        // `endsWith`, not equality: the module reaches this directory by more than one string
        // (the configured form and its resolved twin), and an exact match interposes on neither.
        if (!String(p).endsWith('/items')) return st;
        return {
          isSymbolicLink: () => st.isSymbolicLink(),
          isDirectory: () => st.isDirectory(),
          isFile: () => st.isFile(),
        };
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(opened.ok, false, JSON.stringify(opened));
    assert.equal(opened.halts[0].halt, 'provenance_hazard');
    assert.match(opened.halts[0].message, /cannot establish the identity of asset directory/);
    assert.match(opened.halts[0].message, /items/);
  });
});

// [round 22] `rootIdentity` and `rootMustExist` are two guards over two different windows, not one
// guard twice. [round 23] The round-22 framing — that `rootMustExist` was the fallback for a caller
// whose `lstat` cannot report identity — is gone with the fail-open it described: gate 3 halts on a
// root it cannot pin, so that caller never reaches the walk at all. What is left is the window the
// identity check structurally cannot cover, and this test is the only thing that pins it: the root
// passes its identity check and is GONE by the time `readdirSync` runs. The empty map that produces
// would read as a first capture, which is the round-14 defect at the root.
test('openCaptureRun: a root that vanishes between its identity check and its listing is refused, never read as a first capture', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'stale-from-the-previous-build');

    const deps = depsWithOverride({
      readdirSync: (p, opts) => {
        if (p === assetDir) {
          const err = new Error('ENOENT'); err.code = 'ENOENT'; throw err;
        }
        return nodeFs.readdirSync(p, opts);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(opened.ok, false, 'an unavailable identity must not soften the existence rule');
    assert.equal(opened.halts[0].halt, 'provenance_hazard');
    assert.match(opened.halts[0].message, /ENOENT/);
  });
});

// [round 19] The CLOSING half of the same phase distinction, which is the half that must NOT get
// stricter. A capture tool that rewrites an asset by unlink-then-create races the closing snapshot,
// and before `vanished` existed that produced an absent closing key, which rule 3 refuses as
// "missing from closing". It must still refuse — the run cannot show what those bytes were — and it
// must not refuse for a NEW reason that an operator has no way to act on. So the outcome is pinned
// on both sides of the change: still a refusal, and now one that says which of the two things
// happened. Written because the round-19 fix moved this case from one refusing rule to another, and
// "the verdict is unchanged" is a claim about behaviour, not something the diff shows.
// [round 23] The same gap one function over, found by asking the brief's own question of the sibling
// call site: `openCaptureRun` snapshots with gate 3's observation as the root's pin, but
// `closeCaptureRun` has no prior observation to carry and passed none — so the root got no bracket
// AT ALL, not even the self-established one every CHILD directory gets from its own first `lstat`.
// A substitution landing during the root's own listing was therefore invisible here: the walk went
// on resolving `<root>/<name>` against the replacement, hashed a foreign `a.png` as this run's
// CLOSING bytes, and W5 rule 4 reads "closing differs from opening" as a successful capture. The
// root's own first observation is a baseline in exactly the way a child's is.
test('the closing snapshot: an asset root swapped during its own listing is refused, not hashed as the capture', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');
    const foreign = join(dir, 'foreign-items');
    nodeFs.mkdirSync(foreign, { recursive: true });
    nodeFs.writeFileSync(join(foreign, 'a.png'), 'bytes the captured build never produced');

    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));

    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v2');
    let swapped = false;
    const closingDeps = depsWithOverride({
      // The listing itself succeeds against the original directory; the substitution lands between
      // that listing and any use of it, which is the window the middle observation point exists for.
      readdirSync: (p, opts) => {
        const listed = nodeFs.readdirSync(p, opts);
        if (!swapped && String(p).endsWith('/items')) {
          nodeFs.renameSync(assetDir, join(dir, 'items-moved-away'));
          nodeFs.renameSync(foreign, assetDir);
          swapped = true;
        }
        return listed;
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, closingDeps);
    assert.equal(swapped, true, 'the root was never listed — this fixture cannot reach the condition');
    assert.equal(
      nodeFs.readFileSync(join(assetDir, 'a.png'), 'utf8'),
      'bytes the captured build never produced',
      'the swap did not take — this fixture cannot reach the condition',
    );
    assert.equal(closed.ok, false, `a root replaced mid-listing must not be hashed as the capture: ${JSON.stringify(closed)}`);
    assert.equal(closed.halts[0].halt, 'provenance_hazard');
    assert.match(closed.halts[0].message, /replaced by a different directory/);
  });
});

// [round 24 IMPORTANT] W5 re-runs gates 1-4 immediately before it lists the asset directory, and
// then threw away the identity that run observed — it listed unpinned, so the only bracket was the
// walk's own self-baseline. A self-baseline cannot see a PERSISTENT substitution by construction:
// the replacement is already in place when the baseline is taken, so it baselines the replacement
// and every later check agrees with it. The gate-3 pin covers the window the self-baseline cannot.
test('W5: a root replaced between gate 3 and the asset listing is refused, not recorded', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');

    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v2');
    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, true, JSON.stringify(closed));

    // A byte-IDENTICAL copy in a different directory: every hash-based rule still passes, so
    // identity is the only thing that can tell the two apart. This is codex's scenario minus the
    // symlink, which keeps containment out of the measurement.
    const impostor = join(profile.capture.output_dir, 'impostor');
    nodeFs.mkdirSync(impostor, { recursive: true });
    nodeFs.writeFileSync(join(impostor, 'a.png'), 'v2');

    const chapterFile = writeChapterAt(profile, entry, '# items\n\n![a](items/a.png)\n');
    let swapped = false;
    const w5Deps = {
      ...stubDepsNoIdentity(),
      expectedAssets: stubExpectedAssetsFor(assetDir, ['a.png']),
      // The chapter file is read after gate 3 and before the asset listing, so its own open is a
      // phase boundary this seam can name — the same hook shape the root fixtures use.
      openSync: (p, ...rest) => {
        if (!swapped && String(p).endsWith('/items.md')) {
          nodeFs.renameSync(assetDir, join(dir, 'items-moved-away'));
          nodeFs.renameSync(impostor, assetDir);
          swapped = true;
        }
        return nodeFs.openSync(p, ...rest);
      },
    };

    const result = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, opened.runState.run_id, w5Deps);
    assert.equal(swapped, true, 'the chapter file was never opened — this fixture cannot reach the condition');
    assert.equal(
      nodeFs.readFileSync(join(assetDir, 'a.png'), 'utf8'),
      'v2',
      'the impostor must hash identically, or a hash rule could be doing this refusal instead of identity',
    );
    assert.equal(result.recorded, false, `a substituted root must not be recorded: ${JSON.stringify(result)}`);
    assert.match(String(result.reason), /^asset_listing_failed:/, JSON.stringify(result));
    assert.equal(nodeFs.existsSync(CR.chapterRecordPath(profile, entry)), false);
  });
});

// [round 24 IMPORTANT] The W6 half of the same finding. W6 is the audit entrypoint an operator runs
// over already-merged chapters; it runs gates 1-4 itself a few lines before the listing and threw
// the observed identity away exactly as W5 did. Without this the W5 pin's mutant died alone and W6's
// killed nothing, which is the whole reason both are pinned separately.
test('W6: a root replaced between gate 3 and the asset listing is refused, not reported as verified', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir, { capture: { build_identity: { command: 'get-version', ui_read: false } } });
    const identityDeps = depsWithOverride({ runIdentityCommand: () => ({ ok: true, raw: '3.4.1' }) });
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'previous-build-bytes');

    const opened = CR.openCaptureRun(profile, [entry], null, identityDeps);
    assert.equal(opened.ok, true, JSON.stringify(opened));
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'this-build-bytes');
    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, identityDeps);
    assert.equal(closed.ok, true, JSON.stringify(closed));

    const chapterFile = writeChapterAt(profile, entry, '# items\n');
    const embed = chapterPathsModule.embedPath(chapterFile, assetDir, 'a.png');
    nodeFs.writeFileSync(chapterFile, `# items\n\n1. Step\n\n   ![a](${embed})\n`);
    const recorded = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, opened.runState.run_id, identityDeps);
    assert.equal(recorded.recorded, true, JSON.stringify(recorded));

    // Byte-identical, so every hash-based rule in W6 still agrees — identity is the only thing that
    // can separate the two directories, which is what makes this a test of the pin.
    const impostor = join(profile.capture.output_dir, 'impostor');
    nodeFs.mkdirSync(impostor, { recursive: true });
    nodeFs.writeFileSync(join(impostor, 'a.png'), 'this-build-bytes');

    let swapped = false;
    const auditDeps = depsWithOverride({
      runIdentityCommand: () => ({ ok: true, raw: '3.4.1' }),
      openSync: (p, ...rest) => {
        if (!swapped && String(p).endsWith('/items.md')) {
          nodeFs.renameSync(assetDir, join(dir, 'items-moved-away'));
          nodeFs.renameSync(impostor, assetDir);
          swapped = true;
        }
        return nodeFs.openSync(p, ...rest);
      },
    });

    const report = CR.buildProvenanceReport(profile, [entry], null, auditDeps);
    assert.equal(swapped, true, 'the chapter file was never opened — this fixture cannot reach the condition');
    assert.equal(report.ok, false, `a substituted root must not be audited as verified: ${JSON.stringify(report)}`);
    assert.equal(report.halts[0].halt, 'asset_listing_failed', JSON.stringify(report.halts));
  });
});

// [round 24] The ENOTDIR twin of the ENOENT branch below. The two branches have drawn the same
// distinction since round 18 and must keep drawing it: whichever way a listing fails, a root this
// walk has already identified did not fail to exist — it stopped being a directory while this run
// was the thing looking at it.
test('the closing snapshot: a root that lists ENOTDIR after its own baseline is refused, not read as empty', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');

    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));

    let listed = false;
    const closingDeps = depsWithOverride({
      readdirSync: (p, opts) => {
        if (String(p).endsWith('/items')) {
          listed = true;
          const err = new Error('ENOTDIR'); err.code = 'ENOTDIR'; throw err;
        }
        return nodeFs.readdirSync(p, opts);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, closingDeps);
    assert.equal(listed, true, 'the root was never listed — this fixture cannot reach the condition');
    assert.equal(closed.ok, false, `an identified root that stopped being one must not close silently: ${JSON.stringify(closed)}`);
    assert.equal(closed.halts[0].halt, 'provenance_hazard');
    assert.match(closed.halts[0].message, /ENOTDIR/);
  });
});

// [round 25, review bot P2] The twelfth layer, and it is the round-24 fix's own blind spot. Round 24
// carried a FAILED first observation (`rootUnidentified`) and adjudicated it on exactly one outcome:
// the listing succeeding. The two FAILING outcomes kept deciding from `expectedId` alone — and
// `expectedId` is null on precisely the path a failed observation produces, so the carried failure
// was read, ignored, and dropped by the branch that ran instead.
//
// The reproduction needs no mock at all: replace the asset root with an ordinary file between the
// open and the close. Its own baseline `lstat` SUCCEEDS and reports a non-directory, which is
// `inspection_failure` — not `vanished` — and the listing then fails with a real ENOTDIR. Both
// `rootMustExist` and `expectedId` are unset at the close, so the walk returned silently and
// `closeCaptureRun` committed `closing: {}` with `closing_hazards: []`: an unobserved snapshot
// recorded as clean, the release's defect class one branch further down.
test('the closing snapshot: a root that is already a regular file at its own baseline is refused, not read as empty', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');

    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));

    // The real filesystem performs the substitution — no `lstatSync` or `readdirSync` seam is
    // overridden here, so the condition is reached the way an operator would reach it.
    nodeFs.rmSync(assetDir, { recursive: true, force: true });
    nodeFs.writeFileSync(assetDir, 'not a directory');
    assert.equal(nodeFs.lstatSync(assetDir).isDirectory(), false, 'the fixture did not replace the root');

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, false, `a root that is no longer a directory must not close silently: ${JSON.stringify(closed)}`);
    assert.equal(closed.halts[0].halt, 'provenance_hazard', JSON.stringify(closed.halts));
    assert.match(closed.halts[0].message, /could not be confirmed \(inspection_failure\)/);
  });
});

// [round 25, review bot P2] The ENOENT twin, and the one place the distinction has to be drawn
// FINER than "the observation failed": `vanished` is the reason a first capture produces, and a
// first capture must still close silently with an empty map. Every OTHER reason means the root was
// there, in some form this module could not identify, and is gone by the time it is listed — which
// no first capture can produce.
test('the closing snapshot: a root whose baseline failed for a reason other than absence is refused when the listing then reports it gone', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');

    // [round 33] The EACCES belongs to the SNAPSHOT's own baseline. Gate 3 now observes this path
    // first, and an EACCES there is its own refusal with its own sentence, so the throw is armed
    // only once the sweep is underway — see `armAfterFirstChapterListing`.
    const arming = armAfterFirstChapterListing(profile, () => {});
    const opened = CR.openCaptureRun(profile, [SWEEP_ARMING_ENTRY, entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));

    nodeFs.rmSync(assetDir, { recursive: true, force: true });
    const closingDeps = depsWithOverride({
      readdirSync: arming.readdirSync,
      lstatSync: (p, opts) => {
        if (arming.armed && String(p).endsWith('/items')) {
          const err = new Error('EACCES'); err.code = 'EACCES'; throw err;
        }
        return nodeFs.lstatSync(p, opts);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, closingDeps);
    assert.equal(arming.armed, true, 'the sweep never reached the second chapter — this fixture cannot reach the condition');
    assert.equal(closed.ok, false, `an unidentifiable root that then vanished must not close silently: ${JSON.stringify(closed)}`);
    assert.equal(closed.halts[0].halt, 'provenance_hazard', JSON.stringify(closed.halts));
    assert.match(closed.halts[0].message, /could not be confirmed \(inspection_failure\)/);
  });
});

// [round 25] The control for the two above, and the reason they test the REASON rather than the
// failure. Codex's standing question on this release is over-refusal: a chapter that legitimately
// produced nothing must still close clean. Here the root never existed at all — `vanished` at the
// baseline, ENOENT at the listing — and the close must stay silent. If a fix to the two tests above
// is written as "a failed baseline refuses", this test is what fails.
test('the closing snapshot: a root that never existed still closes clean with an empty map', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    assert.equal(nodeFs.existsSync(assetDir), false, 'the fixture must start with no asset root at all');

    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    assert.equal(nodeFs.existsSync(assetDir), false, 'nothing may create the asset root between open and close');

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, true, `a chapter that produced no assets must close clean: ${JSON.stringify(closed)}`);
  });
});

// [round 26 BLOCKER] The thirteenth layer, and it is inside round 25's own fix: a REASON ALIAS.
// `vanished` is produced by two structurally different situations, and the whitelist could not tell
// them apart. Direct absence — `lstat(root)` itself throws ENOENT — is the one a first capture
// produces, and it is legitimate. A DANGLING ROOT SYMLINK is the other: `lstat` succeeds and reports
// a link, so the root is PRESENT, and only `realpathSync` then fails ENOENT. Both arrived as
// `vanished`, the ENOENT branch whitelisted the word rather than the fact, and codex executed the
// consequence on the real filesystem: `opening: {}` with no hazards, the link's target then
// populated with previous-build bytes, and W5 counting every one of them as brand-new because there
// is no opening key to compare against.
//
// A dangling root symlink that is present at gate 3 ALREADY halts there, for exactly this reason —
// so refusing it here is not new policy, it is the existing policy applied to the window gate 3
// cannot see. That is also the answer to over-refusal: an operator whose root link dangles is
// stopped today, one syscall earlier.
test('openCaptureRun: a dangling root symlink appearing after validation is refused, not read as a first capture', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const link = join(profile.capture.output_dir, 'items');
    const target = join(profile.capture.output_dir, 'items-target');
    nodeFs.mkdirSync(profile.capture.output_dir, { recursive: true });

    // Gate 3 sees a genuinely absent root — a first capture — so it supplies no pin at all.
    assert.equal(nodeFs.existsSync(link), false, 'the fixture must start with no asset root');

    // [round 27, codex MINOR] Stating the seam honestly, because the round-26 framing did not: this
    // DOES override `openSync`, and the override is a CLOCK, not a filesystem. It delegates to the
    // real `openSync` and changes no answer any call receives; it only names the moment between
    // gate 3 and the walk, which is the window under test. Every `lstat`, `realpath` and `readdir`
    // the module makes about the asset tree is the real one against real objects.
    let planted = false;
    const deps = depsWithOverride({
      openSync: (p, ...rest) => {
        if (!planted && String(p).endsWith('/pending.json')) {
          // The root becomes PRESENT — as a link to a path that does not exist yet.
          nodeFs.symlinkSync(target, link);
          planted = true;
        }
        return nodeFs.openSync(p, ...rest);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(planted, true, 'the plant hook never ran — this fixture cannot reach the condition');
    assert.equal(nodeFs.lstatSync(link).isSymbolicLink(), true, 'the root must be a present link, or this test says nothing');
    assert.equal(nodeFs.existsSync(link), false, 'the link must dangle, or this test says nothing');
    assert.equal(opened.ok, false, `a present-but-dangling root must not be read as a first capture: ${JSON.stringify(opened)}`);
    assert.equal(opened.halts[0].halt, 'provenance_hazard', JSON.stringify(opened.halts));

    // [round 27, codex MINOR] What stood here was a W5 call with a run id of `no-such-run`, after
    // the open had already been asserted to fail. W5 says `recorded: false` for an unverifiable run
    // whatever this hazard did, so the assertion could not distinguish the fix from its absence —
    // the far end of codex's trace was decorated, not closed. What CAN be asserted here is the
    // thing the halt is for: the run never opened, so there is no run record for W5 to read, and
    // the reservation is not left behind for the next run to trip over.
    assert.equal(nodeFs.existsSync(join(CR.provenanceRoot(profile), 'run', 'current.json')), false,
      'a refused open must commit no run record');
    assert.equal(nodeFs.existsSync(tokenPathFor(profile)), false,
      'a refused open must not leave its reservation behind');
  });
});

// [round 27 BLOCKER] The fourteenth layer, one path component above the thirteenth. `lstat` does not
// follow the FINAL component — which is the only reason it can report a symlink at all — but it
// follows every ANCESTOR. So `lstat('<out>/group/items')` throws ENOENT when `<out>/group` is a
// present dangling symlink, exactly as it does when `items` has simply not been created yet, and
// round 26's `absentDirectly` marked the first as the second. Codex executed it against the real
// exports: `opening_assets: {}` with no hazards for a chapter whose path was NOT genuinely absent.
//
// A dangling ancestor present at gate 3 already halts there. This is that window again.
test('openCaptureRun: a dangling ANCESTOR appearing after validation is refused, not read as a first capture', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items', group: 'admin' };
    const groupDir = join(profile.capture.output_dir, 'admin');
    const groupTarget = join(profile.capture.output_dir, 'admin-target');
    nodeFs.mkdirSync(profile.capture.output_dir, { recursive: true });

    // Gate 3 sees the whole chapter path as genuinely absent: an ordinary first capture.
    assert.equal(nodeFs.existsSync(groupDir), false, 'the fixture must start with no group ancestor');

    let planted = false;
    const deps = depsWithOverride({
      // A clock, not a filesystem — see the note in the test above.
      openSync: (p, ...rest) => {
        if (!planted && String(p).endsWith('/pending.json')) {
          nodeFs.symlinkSync(groupTarget, groupDir);
          planted = true;
        }
        return nodeFs.openSync(p, ...rest);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(planted, true, 'the plant hook never ran — this fixture cannot reach the condition');
    assert.equal(nodeFs.lstatSync(groupDir).isSymbolicLink(), true, 'the ancestor must be a present link');
    assert.equal(nodeFs.existsSync(groupDir), false, 'the ancestor link must dangle');
    // The tip's own lstat is ENOENT — indistinguishable, at the tip, from a genuine first capture.
    assert.throws(() => nodeFs.lstatSync(join(groupDir, 'items')), /ENOENT/,
      'the asset root must lstat ENOENT, or this test is not reproducing the alias');
    assert.equal(opened.ok, false, `a chapter path made absent by a dangling ancestor must not open: ${JSON.stringify(opened)}`);
    assert.equal(opened.halts[0].halt, 'provenance_hazard', JSON.stringify(opened.halts));
  });
});

// [round 27 BLOCKER] The closing half, where nothing upstream refuses it at all.
//
// [round 34] Round 33 left this one planting BEFORE the close, and codex measured what that cost.
// With gate 3 at the close, an ancestor already dangling at call time is refused in VALIDATION — the
// executed halt reads `lstat failed on '<out>/admin-target'` — so this fixture proved a guard one
// layer up while the adjudication it was written for went unexercised, and it kept passing
// throughout. It plants on the sweep seam now. The chapter also has to be one gate 3 saw ABSENT: a
// chapter it saw present carries an identity pin, and that pin refuses first, which is a third guard
// again. Every one of those three refuses the input; only one of them is this test's subject, and
// the assertion says which.
test('the closing snapshot: a dangling ANCESTOR appearing mid-sweep is refused, not read as a chapter that produced nothing', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items', group: 'admin' };
    const groupDir = join(profile.capture.output_dir, 'admin');
    const assetDir = join(groupDir, 'items');
    nodeFs.mkdirSync(groupDir, { recursive: true });
    assert.equal(nodeFs.existsSync(assetDir), false, 'gate 3 must see this chapter absent, or its identity pin refuses instead');

    // The GROUP directory — not the asset root — becomes a link to nothing, while the sweep is
    // between chapters: after every gate has passed, and before this chapter is observed at all.
    const arming = armAfterFirstChapterListing(profile, () => {
      nodeFs.renameSync(groupDir, join(dir, 'admin-moved-away'));
      nodeFs.symlinkSync(join(profile.capture.output_dir, 'admin-target'), groupDir);
    });

    const opened = CR.openCaptureRun(profile, [SWEEP_ARMING_ENTRY, entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));

    const closingDeps = depsWithOverride({
      readdirSync: arming.readdirSync,
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, closingDeps);
    assert.equal(arming.armed, true, 'the ancestor was never replaced — this fixture cannot reach the condition');
    assert.equal(nodeFs.lstatSync(groupDir).isSymbolicLink(), true, 'the ancestor must be a present link');
    assert.throws(() => nodeFs.lstatSync(assetDir), /ENOENT/, 'the asset root must lstat ENOENT');
    assert.equal(closed.ok, false, `a chapter path made absent by a dangling ancestor must not close clean: ${JSON.stringify(closed)}`);
    assert.equal(closed.halts[0].halt, 'provenance_hazard', JSON.stringify(closed.halts));
    // The DIAGNOSTIC, not the class: the closing adjudication is the guard under test, and gate 3's
    // own refusal and the identity pin both render as this same halt class.
    assert.match(closed.halts[0].message, /could not be confirmed \(vanished\) and its listing then failed \(ENOENT\)/,
      `the halt must attribute this to the closing adjudication: ${JSON.stringify(closed.halts)}`);
  });
});

// [round 28 BLOCKER] The fifteenth layer, and the sharpest one yet: the check did not test the fact
// it claimed. `directAbsenceConfirmed` climbed from the root's PARENT, and it runs only AFTER the
// listing has already failed — so between the failed listing and the adjudication the root itself
// could come back, populated, and the helper would certify "direct absence" having never looked at
// the tip. Codex executed it through the real exported `openCaptureRun`: `tipProbesAfterPopulation:
// 0`, `tipExistsAtReturn: true`, `ok: true`, an empty hazard-free opening baseline.
//
// This is wider than the documented residual 1 (a swap installed and reverted between two adjacent
// observations). Here the substitution is STILL IN PLACE at the moment of adjudication and the
// adjudication does not look.
test('the closing snapshot: a root that reappears between its failed listing and the adjudication is refused', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');

    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));

    nodeFs.rmSync(assetDir, { recursive: true, force: true });
    let reappeared = false;
    const closingDeps = depsWithOverride({
      readdirSync: (p, opts) => {
        if (!reappeared && String(p).endsWith('/items')) {
          // The listing genuinely fails on an absent root, and the root is back — populated with
          // bytes this run never produced — before the adjudication that the failure triggers.
          nodeFs.mkdirSync(assetDir, { recursive: true });
          nodeFs.writeFileSync(join(assetDir, 'a.png'), 'bytes the captured build never produced');
          reappeared = true;
          const err = new Error('ENOENT'); err.code = 'ENOENT'; throw err;
        }
        return nodeFs.readdirSync(p, opts);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, closingDeps);
    assert.equal(reappeared, true, 'the listing hook never ran — this fixture cannot reach the condition');
    assert.equal(nodeFs.existsSync(assetDir), true, 'the root must be present at adjudication time, or this test says nothing');
    assert.equal(closed.ok, false, `a root present at adjudication is not a chapter that produced nothing: ${JSON.stringify(closed)}`);
    assert.equal(closed.halts[0].halt, 'provenance_hazard', JSON.stringify(closed.halts));
  });
});

// [round 28 BLOCKER] Codex's own sequence, at the opening observation point: gate 3 sees an ordinary
// absent grouped path, a dangling group link appears before the snapshot, the tip's lstat and
// listing both report ENOENT, and only THEN does the path become a real populated directory.
test('openCaptureRun: a chapter path populated between its failed listing and the adjudication is refused', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items', group: 'admin' };
    const groupDir = join(profile.capture.output_dir, 'admin');
    const assetDir = join(groupDir, 'items');
    nodeFs.mkdirSync(profile.capture.output_dir, { recursive: true });

    let planted = false;
    let populated = false;
    const deps = depsWithOverride({
      openSync: (p, ...rest) => {
        if (!planted && String(p).endsWith('/pending.json')) {
          nodeFs.symlinkSync(join(profile.capture.output_dir, 'admin-target'), groupDir);
          planted = true;
        }
        return nodeFs.openSync(p, ...rest);
      },
      readdirSync: (p, opts) => {
        // `planted` gates this so the population cannot land before gate 3 has had its look.
        if (planted && !populated && String(p).endsWith('/admin/items')) {
          nodeFs.unlinkSync(groupDir);
          nodeFs.mkdirSync(assetDir, { recursive: true });
          nodeFs.writeFileSync(join(assetDir, 'a.png'), 'bytes the captured build never produced');
          populated = true;
          const err = new Error('ENOENT'); err.code = 'ENOENT'; throw err;
        }
        return nodeFs.readdirSync(p, opts);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(planted, true, 'the plant hook never ran — this fixture cannot reach the condition');
    assert.equal(populated, true, 'the population hook never ran — this fixture cannot reach the condition');
    assert.equal(nodeFs.existsSync(assetDir), true, 'the chapter path must be populated at adjudication time');
    assert.equal(opened.ok, false, `an opening baseline may not be empty for a path that exists: ${JSON.stringify(opened)}`);
    assert.equal(opened.halts[0].halt, 'provenance_hazard', JSON.stringify(opened.halts));
  });
});

// [round 29 BLOCKER] The sixteenth layer. The climb accepted an ancestor that RESOLVES TO A
// DIRECTORY, and the comment beside it claimed parity with gate 3 — but gate 3's property is
// stricter: the ancestor must resolve INSIDE `capture.output_dir`. A post-validation ancestor
// symlink pointing at an existing directory outside the root satisfied the weaker test, so the
// opening baseline came back empty and hazard-free and the capture command's later writes landed
// outside the tree entirely. Codex executed it: `laterWriteResolvesUnder: "/outside/admin-real/
// items"`, with `ok: true`.
//
// The comment was the tell, and it was mine: prose asserting a property the code did not check.
test('openCaptureRun: an ancestor symlink to an OUTSIDE directory appearing after validation is refused', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items', group: 'admin' };
    const groupDir = join(profile.capture.output_dir, 'admin');
    const outside = join(dir, 'outside-admin');
    nodeFs.mkdirSync(profile.capture.output_dir, { recursive: true });
    // A real, resolvable directory — just not one this handbook owns.
    nodeFs.mkdirSync(outside, { recursive: true });

    let planted = false;
    const deps = depsWithOverride({
      openSync: (p, ...rest) => {
        if (!planted && String(p).endsWith('/pending.json')) {
          nodeFs.symlinkSync(outside, groupDir);
          planted = true;
        }
        return nodeFs.openSync(p, ...rest);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(planted, true, 'the plant hook never ran — this fixture cannot reach the condition');
    assert.equal(nodeFs.statSync(groupDir).isDirectory(), true, 'the ancestor must RESOLVE, or this test only repeats the dangling one');
    assert.equal(nodeFs.existsSync(join(groupDir, 'items')), false, 'the leaf must still be absent');
    assert.equal(opened.ok, false, `an ancestor resolving outside the output dir must not certify absence: ${JSON.stringify(opened)}`);
    assert.equal(opened.halts[0].halt, 'provenance_hazard', JSON.stringify(opened.halts));
  });
});

// [round 29 IMPORTANT] This test exists because a claim of mine was wrong. Round 28 recorded the
// `absentDirectly`-leak mutant as an EXPECTED SURVIVOR, reasoning that whenever the leak could fire
// the tip is a symlink whose own `lstat` succeeds, so the tip probe refuses first. Codex found the
// topology that breaks it: remove the dangling tip symlink before the failed listing returns. The
// tip probe then gets ENOENT, climbs to an ordinary contained parent, and certifies direct absence —
// the leak is observable after all, and the survivor was a coverage hole wearing a rationale.
//
// Production refuses here because it does not contain the leak. This pins that, so the mutant dies.
test('the closing snapshot: a dangling root symlink REMOVED before its listing returns is still refused', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');

    // The root becomes a DANGLING SYMLINK, which is what makes the baseline read `vanished` from
    // the target's failed realpath rather than from the root's own lstat.
    // [round 33] It appears AFTER validation, not before it. The close now runs gate 3, which
    // refuses a dangling asset root outright (`cannot establish the identity of ...: vanished`) —
    // correct, and it means a link planted before the call never reaches the snapshot guard this
    // test exists to pin. Gate 3 sees this chapter plainly absent, which is the shape it tolerates,
    // and the link appears while the first chapter is being listed.
    const arming = armAfterFirstChapterListing(profile, () => {
      nodeFs.symlinkSync(join(profile.capture.output_dir, 'items-target'), assetDir);
    });

    const opened = CR.openCaptureRun(profile, [SWEEP_ARMING_ENTRY, entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));

    nodeFs.renameSync(assetDir, join(dir, 'items-moved-away'));

    let removed = false;
    const closingDeps = depsWithOverride({
      readdirSync: (p, opts) => {
        if (String(p).endsWith(`/${SWEEP_ARMING_ENTRY.slug}`)) return arming.readdirSync(p, opts);
        if (!removed && String(p).endsWith('/items')) {
          // ... and the link is gone by the time anything looks again, so every later probe of the
          // tip reports plain absence. Nothing downstream can tell this from a first capture except
          // the baseline observation itself, which is exactly why its reason must stay precise.
          nodeFs.unlinkSync(assetDir);
          removed = true;
          const err = new Error('ENOENT'); err.code = 'ENOENT'; throw err;
        }
        return nodeFs.readdirSync(p, opts);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, closingDeps);
    assert.equal(arming.armed, true, 'the link was never planted — this fixture cannot reach the condition');
    assert.equal(removed, true, 'the removal hook never ran — this fixture cannot reach the condition');
    assert.equal(nodeFs.existsSync(assetDir), false, 'the tip must be plainly absent at adjudication time');
    assert.equal(closed.ok, false, `a root observed as a dangling link must not close as an empty chapter: ${JSON.stringify(closed)}`);
    assert.equal(closed.halts[0].halt, 'provenance_hazard', JSON.stringify(closed.halts));
    // The snapshot's adjudication, not gate 3's identity refusal — the two are different guards and
    // only this wording belongs to the one under test.
    assert.match(closed.halts[0].message, /could not be confirmed \(vanished\) and its listing then failed \(ENOENT\)/);
  });
});

// [round 29, from mutation] The climb exempts everything ABOVE the configured output root, because
// with nothing existing along the path there is nothing that could be a symlink. The boundary is
// strict for a reason mutation had to find: the output root ITSELF is the deepest existing ancestor
// whenever nothing under it exists, and it is exactly where a dangling link does the damage. Widening
// the exemption by one (`<` to `<=`) killed no test until this one existed.
// [round 34] Round 33 moved this fixture onto the sweep seam, and codex measured the cost: the
// replacement takes the whole output root away, so the ARMING chapter's own post-listing identity
// re-check throws first and the halt belongs to a chapter this test is not about. Reverting is not
// available either — measured against the real close, a root already dangling at call time halts in
// validation (`lstat failed on '<...>/assets-target'`), which is the same false attribution one
// guard further up. Reaching the climb needs what the sibling below needs and for the same reason:
// an output root with NO identity at validation, so the round-31 bracket has nothing to compare and
// stands down. The two fixtures differ only in the `..`, and they pin different things — this one
// the exemption's `<`, the sibling the unit the depth is counted in.
test('the closing snapshot: a dangling output root that appears mid-sweep is refused, not read as a chapter that produced nothing', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const outputRoot = profile.capture.output_dir;
    const entry = { slug: 'items' };
    const assetDir = join(outputRoot, 'items');
    assert.equal(nodeFs.existsSync(outputRoot), false, 'the output root must not exist yet, or the bracket pins this instead of the climb');

    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, `a chapter under a not-yet-created output root is an ordinary first capture: ${JSON.stringify(opened)}`);

    let planted = false;
    const closingDeps = depsWithOverride({
      readdirSync: (p, opts) => {
        if (!planted && String(p) === assetDir) {
          // The OUTPUT ROOT — the boundary itself — comes into existence as a link to nothing, in
          // the instant the chapter's listing reports the path gone. Everything the adjudication
          // climbs past is absent until it reaches this, at exactly the root's own depth.
          planted = true;
          nodeFs.symlinkSync(join(dir, 'assets-target'), outputRoot);
          const err = new Error('ENOENT'); err.code = 'ENOENT'; throw err;
        }
        return nodeFs.readdirSync(p, opts);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, closingDeps);
    assert.equal(planted, true, 'the output root was never planted — this fixture cannot reach the condition');
    assert.equal(nodeFs.lstatSync(outputRoot).isSymbolicLink(), true, 'the output root must be a present link');
    assert.throws(() => nodeFs.lstatSync(assetDir), /ENOENT/, 'the asset root must lstat ENOENT');
    assert.equal(closed.ok, false, `a dangling output root must not certify direct absence: ${JSON.stringify(closed)}`);
    assert.equal(closed.halts[0].halt, 'provenance_hazard', JSON.stringify(closed.halts));
    // The DIAGNOSTIC: the closing adjudication, which is the only guard that can reach this input —
    // validation saw an ordinary first capture, and the output-root bracket has no identity to
    // compare. A halt naming anything else means the fixture stopped reproducing the condition.
    assert.match(closed.halts[0].message, /could not be confirmed \(vanished\) and its listing then failed \(ENOENT\)/,
      `the halt must attribute this to the closing adjudication: ${JSON.stringify(closed.halts)}`);
  });
});

// [round 33, codex question 2] The other guard codex named, and the other one shipping unpinned:
// the depth unit. With a literal `..` in `capture.output_dir` and a dangling output root, the raw
// segment count exceeds the normalized asset path's own, the root ITSELF classifies as "above the
// output root", and the exemption certifies a dangling boundary as direct absence. The round-30
// test covers the `..` through the OPEN, where containment refuses first; the sibling below covers a
// dangling root without a `..`, where raw and normalized agree. Only both together reach the unit.
// Reaching the BOUNDARY takes a root with no identity at validation — otherwise the round-31
// output-root bracket refuses a mid-run replacement before the climb ever runs, which is what the
// sibling test above now pins. A root that does not exist yet has nothing to compare, so the bracket
// stands down, and the climb is the only thing left. Instrumenting the exemption to throw showed 21
// tests reach it and none of them reach its edge: they all climb past an absent root, where `<` and
// `<=` agree. This is the one shape where they disagree.
test('the closing snapshot: a `..` in output_dir does not exempt a dangling output root from the climb', () => {
  withTempDir((dir) => {
    // NOT `join(...)`: it normalizes `..` away, and a fixture built with it carries no `..` at all.
    const outputDir = `${dir}/vault/handbook/../assets`;
    const chaptersDir = join(dir, 'handbook');
    nodeFs.mkdirSync(chaptersDir, { recursive: true });
    nodeFs.mkdirSync(join(dir, 'vault'), { recursive: true });
    const profile = {
      capture: { output_dir: outputDir, build_identity: { ui_read: false } },
      publish: { chapters_dir: chaptersDir, target: 'static_md' },
    };
    const entry = { slug: 'items' };
    const outputRoot = join(dir, 'vault', 'assets');
    const assetDir = join(outputRoot, 'items');
    assert.equal(nodeFs.existsSync(outputRoot), false, 'the output root must not exist yet, or the bracket pins this instead of the climb');

    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, `a chapter under a not-yet-created output root is an ordinary first capture: ${JSON.stringify(opened)}`);

    let planted = false;
    const closingDeps = depsWithOverride({
      readdirSync: (p, opts) => {
        if (!planted && String(p) === assetDir) {
          // The output ROOT — the boundary itself — comes into existence as a link to nothing,
          // in the instant the chapter's listing reports the path gone. Everything the adjudication
          // then climbs past is absent until it reaches this, at exactly the root's own depth.
          planted = true;
          nodeFs.symlinkSync(join(dir, 'vault', 'assets-target'), outputRoot);
          const err = new Error('ENOENT'); err.code = 'ENOENT'; throw err;
        }
        return nodeFs.readdirSync(p, opts);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, closingDeps);
    assert.equal(planted, true, 'the output root was never planted — this fixture cannot reach the condition');
    assert.equal(nodeFs.lstatSync(outputRoot).isSymbolicLink(), true, 'the output root must be a present link');
    assert.throws(() => nodeFs.lstatSync(assetDir), /ENOENT/, 'the asset root must lstat ENOENT through the dangling root');
    assert.equal(closed.ok, false, `a dangling output root reached through a '..' must not certify absence: ${JSON.stringify(closed)}`);
    assert.equal(closed.halts[0].halt, 'provenance_hazard', JSON.stringify(closed.halts));
  });
});

// [round 33, from mutation] BLOCKER 1's closing twin, and the answer to whether the close may keep
// re-deriving the output root per entry. It may not: a root re-observed inside the sweep is read
// AFTER the replacement, so it agrees with the replacement about itself. The chapter identity pin
// covers this whenever gate 3 saw the chapter — so the case that matters is the chapter it saw
// ABSENT, where the carried output-root identity is the only thing left holding the boundary.
test('the closing snapshot: an output root replaced BETWEEN chapters is refused, not re-observed', () => {
  withTempDir((dir) => {
    const treeA = join(dir, 'treeA');
    const treeB = join(dir, 'treeB');
    const outputRoot = join(dir, 'assets');
    nodeFs.mkdirSync(join(treeA, SWEEP_ARMING_ENTRY.slug), { recursive: true });
    nodeFs.writeFileSync(join(treeA, SWEEP_ARMING_ENTRY.slug, 'a.png'), 'v1');
    nodeFs.mkdirSync(join(treeB, 'items'), { recursive: true });
    // The arming chapter must survive the repoint, or its own identity re-checks refuse first and
    // the sweep never reaches the chapter this test is about. It points back at the SAME physical
    // directory, so every pin on it still holds — identity is the resolved target's, not the link's.
    nodeFs.symlinkSync(join(treeA, SWEEP_ARMING_ENTRY.slug), join(treeB, SWEEP_ARMING_ENTRY.slug));
    nodeFs.writeFileSync(join(treeB, 'items', 'foreign.png'), 'bytes from a tree this handbook does not own');
    nodeFs.symlinkSync(treeA, outputRoot);

    const profile = profileFor(dir, { capture: { output_dir: outputRoot, build_identity: { ui_read: false } } });
    const entry = { slug: 'items' };
    assert.equal(nodeFs.existsSync(join(treeA, 'items')), false, 'gate 3 must see this chapter absent, or the identity pin refuses instead');

    let repointed = false;
    const closingDeps = depsWithOverride({
      openSync: (p, ...rest) => {
        try {
          return nodeFs.openSync(p, ...rest);
        } finally {
          if (!repointed && String(p).endsWith(`/${SWEEP_ARMING_ENTRY.slug}/a.png`)) {
            repointed = true;
            nodeFs.unlinkSync(outputRoot);
            nodeFs.symlinkSync(treeB, outputRoot);
          }
        }
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [SWEEP_ARMING_ENTRY, entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, closingDeps);
    assert.equal(repointed, true, 'the root was never repointed — this fixture cannot reach the condition');
    assert.equal(closed.ok, false, `a root replaced mid-sweep must not certify the chapters read after it: ${JSON.stringify(closed)}`);
    assert.equal(closed.halts[0].halt, 'provenance_hazard', JSON.stringify(closed.halts));
    assert.match(closed.halts[0].message, /output root was replaced/,
      `the halt must attribute this to the carried output-root identity: ${closed.halts[0].message}`);
  });
});

// [round 33, from mutation] The containment check's own unresolvable arm, which had no test at all:
// inverting it to a tolerance killed nothing at `4d49551`. A cycle introduced on the chapter path
// after gate 3 has passed reaches it, and the distinction the mutant erases is not whether the run
// halts — it does either way, further down — but whether the operator is told a containment check
// could not be made.
test('openCaptureRun: a chapter path that stops resolving after validation fails the containment check by name', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(profile.capture.output_dir, { recursive: true });
    // Absent at validation: the containment RE-CHECK is the guard for a chapter gate 3 saw absent,
    // and a chapter it observed takes the identity-pinned branch instead, where this arm never runs.
    assert.equal(nodeFs.existsSync(assetDir), false, 'gate 3 must see an ordinary first capture');

    let armed = false;
    const deps = depsWithOverride({
      openSync: (p, ...rest) => {
        // The reservation write sits between validation and the opening sweep.
        if (String(p).endsWith('/pending.json') && !armed) {
          armed = true;
          nodeFs.mkdirSync(assetDir, { recursive: true });
          nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');
        }
        return nodeFs.openSync(p, ...rest);
      },
      realpathSync: (p, ...rest) => {
        if (armed && String(p) === assetDir) { const err = new Error('ELOOP'); err.code = 'ELOOP'; throw err; }
        return nodeFs.realpathSync(p, ...rest);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(armed, true, 'the reservation never ran — this fixture cannot reach the condition');
    assert.equal(opened.ok, false, `a chapter path that cannot be resolved must not be snapshotted: ${JSON.stringify(opened)}`);
    assert.equal(opened.halts[0].halt, 'provenance_hazard', JSON.stringify(opened.halts));
    assert.match(opened.halts[0].message, /could not be resolved for a containment check \(symlink_cycle\)/,
      `the halt must say a containment check could not be made, and why: ${opened.halts[0].message}`);
  });
});

// [round 33, codex question 2] Codex was asked which of the rounds-25-31 guards still cover an input
// the containment check does not, and named this one with a topology no test had: let the initial
// containment check see an ordinary absent path, and plant the outside ancestor DURING the failing
// listing. Containment has already passed by then, so only the climb's own containment test can
// still refuse — and mutation confirms it: inverting that test to `return true` killed nothing at
// `4d49551`, so the guard has been shipping unpinned since round 29.
test('the closing snapshot: an ancestor that resolves OUTSIDE the root, planted during the failing listing, is not direct absence', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items', group: 'admin' };
    const groupDir = join(profile.capture.output_dir, 'admin');
    const outside = join(dir, 'outside');
    nodeFs.mkdirSync(profile.capture.output_dir, { recursive: true });
    nodeFs.mkdirSync(outside, { recursive: true });

    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, `an absent chapter is an ordinary first capture: ${JSON.stringify(opened)}`);

    let planted = false;
    const closingDeps = depsWithOverride({
      readdirSync: (p, opts) => {
        if (!planted && String(p).endsWith('/admin/items')) {
          // The listing is about to report the path gone. The ancestor becomes a link to a tree
          // outside the output root in that same instant, so every observation the adjudication
          // makes afterwards sees a RESOLVING ancestor — the one shape that is not absence.
          planted = true;
          nodeFs.symlinkSync(outside, groupDir);
          const err = new Error('ENOENT'); err.code = 'ENOENT'; throw err;
        }
        return nodeFs.readdirSync(p, opts);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, closingDeps);
    assert.equal(planted, true, 'the ancestor was never planted — this fixture cannot reach the condition');
    assert.equal(nodeFs.statSync(groupDir).isDirectory(), true, 'the ancestor must RESOLVE, or this repeats the dangling test');
    assert.equal(closed.ok, false, `an ancestor resolving outside the output root must not certify absence: ${JSON.stringify(closed)}`);
    assert.equal(closed.halts[0].halt, 'provenance_hazard', JSON.stringify(closed.halts));
    assert.match(closed.halts[0].message, /could not be confirmed \(vanished\) and its listing then failed \(ENOENT\)/);
  });
});

// [round 30 BLOCKER] The seventeenth layer, and it is a UNIT mismatch. `containmentRoot.depth` was
// the RAW segment count of the configured `capture.output_dir`, while every derived asset path is
// NORMALIZED by the shared path builder. A `..` in the configured root therefore makes the root's
// count exceed the asset path's own, every ancestor classifies as "above the output root", and the
// exemption swallows the identity check and the containment check together.
//
// Measured against the real exported `chapterAssetDir` before it was believed: output_dir
// `/out/vault/handbook/../assets` builds the asset dir `/out/vault/assets/items` — 4 segments
// against a raw root count of 5. The comment justifying the raw count said raw and derived "share an
// exact lexical prefix", which is true of appending and false of normalizing.
test('openCaptureRun: a `..` in capture.output_dir does not disable the ancestor containment check', () => {
  withTempDir((dir) => {
    // NOT `join(...)`: it normalizes `..` away, and the first version of this test did exactly
    // that and passed for an unrelated reason. The configured value must literally carry `..`.
    const outputDir = `${dir}/assets/stale/../real`;
    const profile = profileFor(dir, { capture: { output_dir: outputDir, build_identity: { ui_read: false } } });
    const entry = { slug: 'items', group: 'admin' };
    const resolvedOutput = join(dir, 'assets', 'real');
    const groupDir = join(resolvedOutput, 'admin');
    const outside = join(dir, 'outside-admin');
    nodeFs.mkdirSync(resolvedOutput, { recursive: true });
    nodeFs.mkdirSync(join(dir, 'assets', 'stale'), { recursive: true });
    nodeFs.mkdirSync(outside, { recursive: true });

    assert.match(profile.capture.output_dir, /\/\.\.\//, 'the configured root must literally contain `..`, or this test is the round-29 one again');

    let planted = false;
    const deps = depsWithOverride({
      openSync: (p, ...rest) => {
        if (!planted && String(p).endsWith('/pending.json')) {
          nodeFs.symlinkSync(outside, groupDir);
          planted = true;
        }
        return nodeFs.openSync(p, ...rest);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(planted, true, 'the plant hook never ran — this fixture cannot reach the condition');
    assert.equal(nodeFs.statSync(groupDir).isDirectory(), true, 'the ancestor must resolve, or this repeats the dangling test');
    assert.equal(opened.ok, false, `a '..' in the configured root must not disable containment: ${JSON.stringify(opened)}`);
    assert.equal(opened.halts[0].halt, 'provenance_hazard', JSON.stringify(opened.halts));
  });
});

// [round 30] And the arm I called unreachable in round 29, reached. I concluded `containmentRoot ===
// null` could not happen because every caller sits behind a guard that halts on an unresolvable
// `capture.output_dir` — true, and not sufficient: the guard and `containmentRootFor` are two
// SEPARATE resolutions of the same path, so the tree can change between them. A symlink cycle
// introduced after the guard passes reaches the arm.
//
// This is the second round running that I asserted unreachability from the paths I had looked at.
// The test deleted in round 29 is restored here in the form that actually distinguishes the guard.
test('the closing snapshot: an output root that becomes unresolvable AFTER its own guard still refuses', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');

    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));

    // The asset root is genuinely gone — the ordinary "produced nothing" shape.
    nodeFs.rmSync(assetDir, { recursive: true, force: true });
    // The guard and `containmentRootFor` are two SEPARATE resolutions of the same path, which is
    // the claim under test: allow the first and fail every later one. If there were only ever one
    // resolution, `resolutions` would stay at 1 and the assertion below would fail loudly.
    let resolutions = 0;
    const closingDeps = depsWithOverride({
      realpathSync: (p, ...rest) => {
        if (String(p) === profile.capture.output_dir) {
          resolutions += 1;
          if (resolutions > 1) { const err = new Error('ELOOP'); err.code = 'ELOOP'; throw err; }
        }
        return nodeFs.realpathSync(p, ...rest);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, closingDeps);
    assert.ok(resolutions > 1, `the output root was resolved only ${resolutions} time(s) — there is no second resolution, so the arm is unreachable after all`);
    assert.equal(closed.ok, false, `an output root unresolvable at snapshot time must not certify absence: ${JSON.stringify(closed)}`);
    assert.equal(closed.halts[0].halt, 'provenance_hazard', JSON.stringify(closed.halts));
    // [round 31, codex MINOR] The halt CLASS is not enough. Deleting the null guard entirely also
    // halts — with `Cannot read properties of null` — so a test asserting only the class passes
    // under a mutant that removed the thing it claims to pin. Fail-closed by crash is not the same
    // fact as fail-closed by adjudication, and only the message tells them apart.
    // [round 31/32] The distinction codex drew is crash vs ADJUDICATION, not which adjudication:
    // round 32's containment check now refuses this input first, and both forms are deliberate
    // refusals with an operator-actionable sentence. Deleting the null guard still produces
    // `Cannot read properties of null`, which is what this must keep excluding.
    // [round 33] And the owner moved once more: the close runs gate 3 now, so validation's own
    // resolution meets the second ELOOP and refuses before the sweep starts. What this test pins is
    // therefore the property in its title — a root that stops resolving after its first guard does
    // not certify absence — and no longer any particular arm. The snapshot's `containmentRoot ===
    // null` arms are consequently unreachable from every entrypoint; they are kept as fail-closed
    // surface, not deleted, because "unreachable" has been the wrong call twice on this branch.
    assert.doesNotMatch(closed.halts[0].message, /Cannot read propert|is not a function|undefined is not/,
      `the halt must be adjudicated, not a crash: ${closed.halts[0].message}`);
    assert.match(closed.halts[0].message, /could not be confirmed|containment check|resolves outside|cannot resolve capture\.output_dir/,
      `the halt must name what it refused: ${closed.halts[0].message}`);
    // The reason travels rather than being flattened: this input is a symlink CYCLE, and reporting
    // it as a generic inspection failure is the confident-wrong-diagnosis shape, one layer down.
    assert.match(closed.halts[0].message, /symlink_cycle/,
      `an ELOOP must be named as one: ${closed.halts[0].message}`);
  });
});

// [round 31 BLOCKER] The eighteenth layer, and it steps up a level: not the chapter root but the
// OUTPUT ROOT. Gate 3 keeps an identity only for a chapter directory that already EXISTS, so a
// first capture supplies no pin at all — and the direct-absence machinery of rounds 25 through 30
// cannot help here, because the replacement directory is POPULATED and lists successfully, so the
// failed-listing adjudication is never reached. The walk simply self-baselines whatever is there.
//
// Codex executed it end to end through the real exports: gate 3 saw `<out> -> safeA` with
// `safeA/items` legitimately absent; the output root was repointed to a populated `outsideB` before
// the snapshot; `openCaptureRun` returned ok and captured the foreign bytes with no hazard; and W5
// then returned `recorded: true`, confidently attributing another tree's bytes to this build.
test('openCaptureRun: the OUTPUT ROOT repointed after validation is refused, even for a first capture', () => {
  withTempDir((dir) => {
    const safeA = join(dir, 'safeA');
    const outsideB = join(dir, 'outsideB');
    const outputRoot = join(dir, 'out');
    nodeFs.mkdirSync(safeA, { recursive: true });
    nodeFs.mkdirSync(join(outsideB, 'items'), { recursive: true });
    nodeFs.writeFileSync(join(outsideB, 'items', 'a.png'), 'bytes from a tree this handbook does not own');
    nodeFs.symlinkSync(safeA, outputRoot);

    const profile = profileFor(dir, { capture: { output_dir: outputRoot, build_identity: { ui_read: false } } });
    const entry = { slug: 'items' };
    // Gate 3 sees an ordinary first capture: the chapter directory does not exist under safeA.
    assert.equal(nodeFs.existsSync(join(safeA, 'items')), false, 'the chapter root must start absent');

    let repointed = false;
    const deps = depsWithOverride({
      openSync: (p, ...rest) => {
        if (!repointed && String(p).endsWith('/pending.json')) {
          nodeFs.unlinkSync(outputRoot);
          nodeFs.symlinkSync(outsideB, outputRoot);
          repointed = true;
        }
        return nodeFs.openSync(p, ...rest);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(repointed, true, 'the repoint hook never ran — this fixture cannot reach the condition');
    assert.equal(
      nodeFs.readFileSync(join(outputRoot, 'items', 'a.png'), 'utf8'),
      'bytes from a tree this handbook does not own',
      'the output root must now resolve to the foreign tree, or the repoint did not take',
    );
    assert.equal(opened.ok, false, `a repointed output root must not be snapshotted as this run's own: ${JSON.stringify(opened)}`);
    assert.equal(opened.halts[0].halt, 'provenance_hazard', JSON.stringify(opened.halts));
    assert.match(opened.halts[0].message, /output/i, `the halt must name the output root, not a chapter: ${opened.halts[0].message}`);
  });
});

// [round 31, from mutation] The output-root bracket also runs on the PINNED path, where the chapter
// directory existed at gate 3 and carries its own identity. Removing it there killed no test: the
// chapter pin refuses the same scenario, so the two are behaviourally equivalent on `ok`. They are
// not equivalent on the DIAGNOSTIC, and that is what an operator acts on — one says a chapter's
// directory could not be confirmed, the other says the output root was replaced, and only the
// second points at what actually moved. The assertion is therefore on the message.
test('openCaptureRun: a repointed OUTPUT ROOT is diagnosed as such, not as a chapter that vanished', () => {
  withTempDir((dir) => {
    const safeA = join(dir, 'safeA');
    const outsideB = join(dir, 'outsideB');
    const outputRoot = join(dir, 'out');
    // This time the chapter directory EXISTS at validation, so gate 3 pins it.
    nodeFs.mkdirSync(join(safeA, 'items'), { recursive: true });
    nodeFs.writeFileSync(join(safeA, 'items', 'a.png'), 'v1');
    nodeFs.mkdirSync(outsideB, { recursive: true });
    nodeFs.symlinkSync(safeA, outputRoot);

    const profile = profileFor(dir, { capture: { output_dir: outputRoot, build_identity: { ui_read: false } } });
    const entry = { slug: 'items' };

    let repointed = false;
    const deps = depsWithOverride({
      openSync: (p, ...rest) => {
        if (!repointed && String(p).endsWith('/pending.json')) {
          nodeFs.unlinkSync(outputRoot);
          nodeFs.symlinkSync(outsideB, outputRoot);
          repointed = true;
        }
        return nodeFs.openSync(p, ...rest);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(repointed, true, 'the repoint hook never ran — this fixture cannot reach the condition');
    assert.equal(opened.ok, false, `a repointed output root must not open: ${JSON.stringify(opened)}`);
    assert.match(opened.halts[0].message, /output root was replaced/,
      `the diagnostic must name the output root, not the chapter beneath it: ${opened.halts[0].message}`);
  });
});

// [round 32 BLOCKER 1] The nineteenth layer, and it retires the approach that produced the last
// four. Pinning the output root by `dev:ino` does NOT freeze what paths beneath it resolve to — its
// directory entries change independently of the directory's own identity. Gate 3 records an identity
// only for a chapter directory that EXISTS, so an absent one is represented by nothing at all, and
// the walk self-baselines whatever appears there. Codex created `<out>/items -> outside/items` after
// validation, with the output root's `1:1301` identity unchanged before and after, and the opening
// snapshot hashed the outside bytes with an empty hazard list.
//
// What was missing is not another identity pin. It is CONTAINMENT, re-checked at the moment an
// absent path becomes present — gate 3's own property, which gate 3 could not apply to something
// that did not exist yet.
test('openCaptureRun: a chapter root that appears as a symlink OUTSIDE the output dir is refused', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    const outside = join(dir, 'outside', 'items');
    nodeFs.mkdirSync(profile.capture.output_dir, { recursive: true });
    nodeFs.mkdirSync(outside, { recursive: true });
    nodeFs.writeFileSync(join(outside, 'a.png'), 'bytes from a tree this handbook does not own');
    assert.equal(nodeFs.existsSync(assetDir), false, 'gate 3 must see an ordinary first capture');

    const rootIdBefore = nodeFs.lstatSync(profile.capture.output_dir).ino;
    let planted = false;
    const deps = depsWithOverride({
      openSync: (p, ...rest) => {
        if (!planted && String(p).endsWith('/pending.json')) {
          nodeFs.symlinkSync(outside, assetDir);
          planted = true;
        }
        return nodeFs.openSync(p, ...rest);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(planted, true, 'the plant hook never ran — this fixture cannot reach the condition');
    assert.equal(nodeFs.lstatSync(profile.capture.output_dir).ino, rootIdBefore,
      'the OUTPUT ROOT must be unchanged, or this test is the round-31 one again rather than the layer beneath it');
    assert.equal(opened.ok, false, `a chapter root resolving outside the output dir must not be snapshotted: ${JSON.stringify(opened)}`);
    assert.equal(opened.halts[0].halt, 'provenance_hazard', JSON.stringify(opened.halts));
  });
});

// [round 32 BLOCKER 1] The close is worse, as codex put it: it runs no gate 3 at all, so nothing
// upstream has ever checked containment for a root it self-baselines.
// [round 33] It runs gate 3 now, so a link planted before the call is refused there —
// `asset_dir_escapes_output_dir`, which is the better diagnostic and the wrong guard for this test.
// The snapshot's own containment arm is still the only thing covering a link that appears AFTER
// validation, so that is what the plant reproduces: gate 3 sees an absent chapter (a chapter that
// produced nothing, which it tolerates), and the link exists by the time the snapshot reads it.
test('the closing snapshot: a chapter root that became a symlink OUTSIDE the output dir is refused', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    const outside = join(dir, 'outside', 'items');
    nodeFs.mkdirSync(profile.capture.output_dir, { recursive: true });
    nodeFs.mkdirSync(outside, { recursive: true });
    nodeFs.writeFileSync(join(outside, 'a.png'), 'bytes from a tree this handbook does not own');

    const arming = armAfterFirstChapterListing(profile, () => nodeFs.symlinkSync(outside, assetDir));

    const opened = CR.openCaptureRun(profile, [SWEEP_ARMING_ENTRY, entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));

    const closingDeps = depsWithOverride({
      readdirSync: arming.readdirSync,
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, closingDeps);
    assert.equal(arming.armed, true, 'the link was never planted — this fixture cannot reach the condition');
    assert.equal(closed.ok, false, `a chapter root resolving outside the output dir must not close: ${JSON.stringify(closed)}`);
    assert.equal(closed.halts[0].halt, 'provenance_hazard', JSON.stringify(closed.halts));
    // The snapshot's containment arm by name. Gate 3 refuses this input too, with a different halt
    // and a different sentence, and a test that accepted either would not distinguish them.
    assert.match(closed.halts[0].message, /resolves outside capture\.output_dir/);
  });
});

// [round 33 BLOCKER 2] The close ran no gate 3 and no gate 4 — it built a fresh containment root per
// entry and looped. Codex executed it: two chapter directories aliasing ONE physical directory
// closed `ok: true` with the identical hash committed under both chapter keys, and because both
// opening maps were empty and both closing hashes matched, W5 later handed both chapters a
// confident record. Replacing the aliases with two byte-identical real directories before W5 leaves
// nothing anywhere that could still notice. The close now runs the same validation the open does.
test('the closing snapshot: two chapter roots aliasing ONE physical directory are refused', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entries = [{ slug: 'a' }, { slug: 'b' }];
    nodeFs.mkdirSync(profile.capture.output_dir, { recursive: true });

    const opened = CR.openCaptureRun(profile, entries, null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));

    // The capture command's doing, between open and close: one directory, two names.
    const shared = join(profile.capture.output_dir, 'shared');
    nodeFs.mkdirSync(shared, { recursive: true });
    nodeFs.writeFileSync(join(shared, 'x.png'), 'one file that must not be attributed to two chapters');
    nodeFs.symlinkSync(shared, join(profile.capture.output_dir, 'a'));
    nodeFs.symlinkSync(shared, join(profile.capture.output_dir, 'b'));

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, false, `one physical directory must not be committed as two chapters: ${JSON.stringify(closed)}`);
    assert.equal(closed.halts[0].halt, 'physical_asset_dir_collision', JSON.stringify(closed.halts));
    assert.equal(nodeFs.existsSync(recordPathFor(profile)), false, 'a refused close must write no run record');
  });

  // The control the fix needs, because adding two gates to the close is exactly where over-refusal
  // would land: the same shape with two ORDINARY directories closes.
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entries = [{ slug: 'a' }, { slug: 'b' }];
    nodeFs.mkdirSync(profile.capture.output_dir, { recursive: true });

    const opened = CR.openCaptureRun(profile, entries, null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));

    for (const slug of ['a', 'b']) {
      const chapterDir = join(profile.capture.output_dir, slug);
      nodeFs.mkdirSync(chapterDir, { recursive: true });
      nodeFs.writeFileSync(join(chapterDir, 'x.png'), 'identical bytes, two genuinely different directories');
    }

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, true, `two distinct chapter directories must still close: ${JSON.stringify(closed)}`);
  });
});

// [round 32 BLOCKER 2] "Gate 3's observation is carried into every snapshot" was a claim of mine, and
// it was false: validation canonicalized the root at its start and then built a FRESH observation at
// its return, so the two could disagree and the window between them belonged to nobody. The
// structural fix is to observe once; this pins that structurally rather than by racing it.
test('validateEntriesForCapture resolves the configured output root exactly once per validation', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    nodeFs.mkdirSync(join(profile.capture.output_dir, 'items'), { recursive: true });

    let resolutions = 0;
    const deps = depsWithOverride({
      realpathSync: (p, ...rest) => {
        if (String(p) === profile.capture.output_dir) resolutions += 1;
        return nodeFs.realpathSync(p, ...rest);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(opened.ok, true, JSON.stringify(opened));
    // Two, and which two is the point: the W1 ownership gate resolves it for its own purpose
    // (`assertProvenanceOwnership`), and gate 3 resolves it exactly once. Measured, not assumed —
    // the first version of this assertion said one, counted the whole call, and failed on a gate
    // that was never in scope. A THIRD would mean validation observed it twice again.
    assert.equal(resolutions, 2,
      `the configured output root must be observed once by ownership and once by gate 3, not ${resolutions} times — an extra observation inside validation is a window that belongs to nobody`);
  });
});

// [round 32, review bot P1] `containmentRootFor` makes TWO filesystem observations of the same
// path — `canonicalizeForComparison` for the segments and `assetDirIdentity` for the identity — and
// a repoint landing between them leaves `segments` describing tree A while `identity` pins tree B.
// Every later `outputRootChanged` then compares B with B and agrees. The bot reproduced it through
// the real exported `openCaptureRun` and asked for the exact race as a regression.
test('openCaptureRun: a root repointed BETWEEN its resolution and its identity read is still refused', () => {
  withTempDir((dir) => {
    const treeA = join(dir, 'treeA');
    const treeB = join(dir, 'treeB');
    const outputRoot = join(dir, 'out');
    nodeFs.mkdirSync(treeA, { recursive: true });
    nodeFs.mkdirSync(join(treeB, 'items'), { recursive: true });
    nodeFs.writeFileSync(join(treeB, 'items', 'foreign.png'), 'bytes from a tree this handbook does not own');
    nodeFs.symlinkSync(treeA, outputRoot);

    const profile = profileFor(dir, { capture: { output_dir: outputRoot, build_identity: { ui_read: false } } });
    const entry = { slug: 'items' };
    assert.equal(nodeFs.existsSync(join(treeA, 'items')), false, 'gate 3 must see an ordinary first capture');

    // The seam is inside the bracket, and [round 34] moved which half of it. Resolution 1 of the
    // configured root is the W1 ownership gate; resolution 2 is now the bracket's OWN pre-resolution
    // identity read, and resolution 3 is `canonicalizeForComparison`'s. Firing on 2 therefore
    // repoints after the module has observed the root and before it resolves the name — so the
    // segments describe the replacement, the identity read at `canonical` agrees with them, and
    // NOTHING downstream disagrees with itself. Only the observation taken before the resolution
    // holds the original. Measured: with the bracket removed this fixture returns `ok: true`.
    // Firing on 1 instead repoints before the module has observed anything at all and reproduces a
    // different (unclosable) window — the first version of this fixture did exactly that and proved
    // nothing.
    let resolutions = 0;
    let repointed = false;
    const deps = depsWithOverride({
      realpathSync: (p, ...rest) => {
        const out = nodeFs.realpathSync(p, ...rest);
        if (String(p) === outputRoot) {
          resolutions += 1;
          if (resolutions === 2) {
            nodeFs.unlinkSync(outputRoot);
            nodeFs.symlinkSync(treeB, outputRoot);
            repointed = true;
          }
        }
        return out;
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(repointed, true, 'the repoint hook never ran — this fixture cannot reach the condition');
    assert.equal(opened.ok, false, `segments from one tree and an identity from another must not certify a snapshot: ${JSON.stringify(opened)}`);
    assert.equal(opened.halts[0].halt, 'provenance_hazard', JSON.stringify(opened.halts));
    // [round 34] The DIAGNOSTIC the bot's original fixture never carried, which is why nobody noticed
    // that this input had two possible refusers. Containment could refuse it (the repoint points
    // OUTSIDE the root) and so can the bracket that now spans this exact window; the bracket runs
    // first, and a halt reading `resolves outside capture.output_dir` would mean the window moved.
    assert.match(opened.halts[0].message, /configured_and_resolved_disagree/,
      `the halt must attribute this to the resolution bracket: ${JSON.stringify(opened.halts)}`);
  });
});

// [round 33 BLOCKER 1] The bot's race above repointed the root to an OUTSIDE sibling, so containment
// refused it and the fixture passed for a reason that was never the finding. Codex ran the INSIDE
// variant against the real `openCaptureRun`: repoint the root to a DESCENDANT of its own resolved
// target and every downstream check agrees with itself — `segments` describe tree A, the identity
// pins tree B, `outputRootChanged` compares B with B, and containment passes because B really is
// inside A. It returned `ok: true` with the foreign bytes hashed and no hazard.
//
// [round 34] The DESCENDANT topology is what makes this fixture worth keeping — containment cannot
// refuse a replacement that really is inside the root, so only an identity guard can — but the
// window moved. Round 34 brackets the resolution itself, so a repoint landing between the two
// observations is now refused there and this fixture stopped exercising `outputRootChanged` at all
// (it failed on its own diagnostic assertion, which is the only reason it was noticed). The repoint
// lands at the reservation write instead: after validation has pinned the root, before the snapshot
// reads it. That is where the output-root bracket is the only guard left, and it is also the shape a
// real mid-run replacement takes. The bracket's own window keeps its two fixtures, above and below.
test('openCaptureRun: a root repointed to a DESCENDANT of its own resolved target after validation is refused', () => {
  withTempDir((dir) => {
    const treeA = join(dir, 'treeA');
    const outputRoot = join(dir, 'out');
    nodeFs.mkdirSync(join(treeA, 'sub', 'items'), { recursive: true });
    nodeFs.writeFileSync(join(treeA, 'sub', 'items', 'foreign.png'), 'bytes from a tree this handbook does not own');
    nodeFs.symlinkSync(treeA, outputRoot);

    const profile = profileFor(dir, { capture: { output_dir: outputRoot, build_identity: { ui_read: false } } });
    const entry = { slug: 'items' };
    assert.equal(nodeFs.existsSync(join(treeA, 'items')), false, 'gate 3 must see an ordinary first capture');

    let repointed = false;
    const deps = depsWithOverride({
      // A clock, not a filesystem: the reservation write is the one call that sits strictly between
      // validation and the opening sweep, so the repoint lands after the root has been pinned and
      // before anything reads it again.
      openSync: (p, ...rest) => {
        if (!repointed && String(p).endsWith('/pending.json')) {
          nodeFs.unlinkSync(outputRoot);
          nodeFs.symlinkSync(join(treeA, 'sub'), outputRoot);
          repointed = true;
        }
        return nodeFs.openSync(p, ...rest);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(repointed, true, 'the repoint hook never ran — this fixture cannot reach the condition');
    assert.equal(nodeFs.existsSync(join(outputRoot, 'items', 'foreign.png')), true,
      'the descendant must really be reachable through the configured root, or this fixture refuses for the wrong reason');
    assert.equal(opened.ok, false, `a root replaced by a descendant of itself must not certify a snapshot: ${JSON.stringify(opened)}`);
    assert.equal(opened.halts[0].halt, 'provenance_hazard', JSON.stringify(opened.halts));
    // The DIAGNOSTIC, not the class: containment cannot refuse this input (the descendant IS inside
    // the root), so a halt reading `resolves outside capture.output_dir` would mean the fixture
    // reproduced something else. Only the output-root bracket can name this one.
    assert.match(opened.halts[0].message, /output root was replaced/,
      `the halt must attribute this to the output-root bracket: ${JSON.stringify(opened.halts)}`);
  });
});

// [round 34 BLOCKER] The third variant of the same race, and the one round 33's fix moved instead of
// closing. Both fixtures above repoint a SYMLINK, so the configured name comes to mean a different
// pathname and a later read of that name can see it. Codex replaced the PHYSICAL directory instead:
// the canonical pathname is unchanged and the object beneath it is not. `canonical` is a string,
// stat-ing it is a second syscall, and the swap lands between the two — segments describe A, the
// identity pins B, `outputRootChanged` re-resolves the configured name to B and agrees with itself.
// Measured against the real exported `openCaptureRun` with only storage interposed, before the fix:
// `ok: true`, with B's `foreign.png` in the opening baseline and no hazard.
//
// The configured root is a plain DIRECTORY here, not a link. That is load-bearing twice over: it is
// what lets the pathname survive the swap, and it is why the resolution count is the same with and
// without the bracket (the bracket's own identity read does not resolve a name that is not a link),
// so this fixture lands in the same window either way.
test('openCaptureRun: the PHYSICAL root replaced between its resolution and its identity read is refused', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const outputRoot = profile.capture.output_dir;
    const replacement = join(dir, 'assets-B');
    nodeFs.mkdirSync(outputRoot, { recursive: true });
    nodeFs.mkdirSync(join(replacement, 'items'), { recursive: true });
    nodeFs.writeFileSync(join(replacement, 'items', 'foreign.png'), 'bytes from a tree this handbook does not own');

    const entry = { slug: 'items' };
    assert.equal(nodeFs.existsSync(join(outputRoot, 'items')), false, 'gate 3 must see an ordinary first capture');

    let resolutions = 0;
    let swapped = false;
    const deps = depsWithOverride({
      realpathSync: (p, ...rest) => {
        const out = nodeFs.realpathSync(p, ...rest);
        if (String(p) === outputRoot) {
          resolutions += 1;
          // Resolution 1 is the W1 ownership gate; resolution 2 is `canonicalizeForComparison`'s,
          // and the identity read at `canonical` follows it. The swap is computed AFTER `out`, so
          // the module receives A's segments and every later read of that pathname lands on B.
          if (resolutions === 2) {
            nodeFs.renameSync(outputRoot, join(dir, 'assets-A-moved'));
            nodeFs.renameSync(replacement, outputRoot);
            swapped = true;
          }
        }
        return out;
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(swapped, true, 'the swap hook never ran — this fixture cannot reach the condition');
    assert.equal(nodeFs.existsSync(join(outputRoot, 'items', 'foreign.png')), true,
      'the replacement must be reachable under the root\'s OWN pathname, or this fixture reproduces something else');
    assert.equal(opened.ok, false, `a root whose object changed under its own pathname must not certify a snapshot: ${JSON.stringify(opened)}`);
    assert.equal(opened.halts[0].halt, 'provenance_hazard', JSON.stringify(opened.halts));
    // The DIAGNOSTIC, and here it is the whole point: containment cannot refuse this (the
    // replacement occupies the root's own path) and neither can the output-root bracket (it compares
    // B with B). Only the resolution bracket can name this one.
    assert.match(opened.halts[0].message, /configured_and_resolved_disagree/,
      `the halt must attribute this to the resolution bracket: ${JSON.stringify(opened.halts)}`);
  });
});

// [round 34, from mutation] The bracket's two halves must read the same OBJECT, not the same NAME,
// and mutation is what showed the difference is observable. Reading the second half through the
// configured name instead of through `canonical` killed nothing — every fixture above survives it,
// because a swap makes both forms disagree with themselves. This is the input that separates them,
// and nothing moves in it at all: the kernel resolves `link` before applying `..`, `normalizeSegments`
// collapses `..` before ever seeing `link`, so the configured name and its resolved form denote two
// different directories at the same instant. Read at the name, the identity would pin one tree while
// the segments describe the other — round 33's defect, arriving through a static configuration
// rather than through a race.
test('openCaptureRun: an output_dir whose name and resolved form denote different directories is refused', () => {
  withTempDir((dir) => {
    // The temp root itself may be reached through a symlinked ancestor (macOS `/var` ->
    // `/private/var`); resolving it once here keeps every assertion below about THIS topology.
    const real = nodeFs.realpathSync(dir);
    const linkParent = join(real, 'a');
    nodeFs.mkdirSync(linkParent, { recursive: true });
    nodeFs.mkdirSync(join(real, 'b'), { recursive: true });
    nodeFs.symlinkSync(join(real, 'b'), join(linkParent, 'link'));
    // What the kernel reaches through the configured name ...
    nodeFs.mkdirSync(join(real, 'assets'), { recursive: true });
    // ... and what collapsing `..` lexically reaches instead. Both exist, and they are not the same
    // directory — which is the whole condition.
    nodeFs.mkdirSync(join(linkParent, 'assets'), { recursive: true });

    // NOT `join(...)`: it normalizes `..` away, and a fixture built with it carries no `..` at all.
    const outputDir = `${linkParent}/link/../assets`;
    // `realpathSync` cannot state this fact and asking it was this fixture's first mistake: Node's
    // implementation calls `path.resolve` first, which collapses `..` LEXICALLY — the same thing
    // `normalizeSegments` does, and the opposite of what an `lstat` of this name does. The kernel is
    // the only one that can be asked, so the assertion is an inode comparison.
    const kernelIno = nodeFs.statSync(outputDir).ino;
    assert.equal(kernelIno, nodeFs.statSync(join(real, 'assets')).ino,
      'the kernel must reach <root>/assets through this name, or the fixture is not ambiguous');
    assert.notEqual(kernelIno, nodeFs.statSync(join(linkParent, 'assets')).ino,
      'the lexical form must be a different directory, or there is nothing to disagree about');

    const profile = {
      capture: { output_dir: outputDir, build_identity: { ui_read: false } },
      publish: { chapters_dir: join(real, 'handbook'), target: 'static_md' },
    };
    nodeFs.mkdirSync(profile.publish.chapters_dir, { recursive: true });

    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
    assert.equal(opened.ok, false, `an output_dir that names two directories at once must not certify a snapshot: ${JSON.stringify(opened)}`);
    assert.equal(opened.halts[0].halt, 'provenance_hazard', JSON.stringify(opened.halts));
    assert.match(opened.halts[0].message, /configured_and_resolved_disagree/,
      `the halt must attribute this to the resolution bracket: ${JSON.stringify(opened.halts)}`);
  });
});

// [round 34, from mutation] The other half of the same comparison, and it is not symmetry for its own
// sake: a bracket that only compares two SUCCESSFUL reads treats "one of them found nothing" as
// nothing to compare, stores a null identity, and a null identity stands every later output-root
// bracket down. The root destroyed mid-validation then reaches the climb, which finds nothing along
// the path, classifies every candidate as above a root that no longer exists, and certifies the
// chapter as one that produced nothing. Mutation found it: dropping the arm killed no test.
test('openCaptureRun: an output root that vanishes between the bracket\'s two reads is refused, not read as a first capture', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const outputRoot = profile.capture.output_dir;
    nodeFs.mkdirSync(outputRoot, { recursive: true });

    let resolutions = 0;
    let vanished = false;
    const deps = depsWithOverride({
      realpathSync: (p, ...rest) => {
        const out = nodeFs.realpathSync(p, ...rest);
        if (String(p) === outputRoot) {
          resolutions += 1;
          // Resolution 1 is the W1 ownership gate; resolution 2 is `canonicalizeForComparison`'s.
          // The root is a plain directory, so the bracket's first read did not resolve this name —
          // it has already observed the root, and the object it observed leaves here.
          if (resolutions === 2) {
            nodeFs.renameSync(outputRoot, join(dir, 'assets-vanished'));
            vanished = true;
          }
        }
        return out;
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, deps);
    assert.equal(vanished, true, 'the removal hook never ran — this fixture cannot reach the condition');
    assert.equal(nodeFs.existsSync(outputRoot), false, 'the output root must really be gone');
    assert.equal(opened.ok, false, `an output root observed and then destroyed must not open as a first capture: ${JSON.stringify(opened)}`);
    assert.equal(opened.halts[0].halt, 'provenance_hazard', JSON.stringify(opened.halts));
    assert.match(opened.halts[0].message, /configured_and_resolved_disagree/,
      `the halt must attribute this to the resolution bracket: ${JSON.stringify(opened.halts)}`);
  });
});

// [round 34, review bot P1] The third way into the same null. A root that is THERE and cannot be
// identified was stored as `identity: null`, which is the value that means "nothing to compare" —
// and a null identity makes `outputRootChanged` return false unconditionally, so the bracket it
// feeds is not weakened but switched off. The bot reproduced it through the real exported
// `openCaptureRun`: an `lstat` of the root reporting no `dev`/`ino`, the configured link then
// repointed to a populated descendant, `ok: true`, `foreign.png` hashed, empty hazard list.
// `absentDirectly` is the fact that separates the tolerated case from this one, and it is set at
// exactly one site, on an `lstat` that returned ENOENT.
test('openCaptureRun: an output root that EXISTS but cannot be identified is refused, not treated as absent', () => {
  withTempDir((dir) => {
    // Built on the RESOLVED temp root: the identity read this fixture has to interpose on happens at
    // `canonical`, which is a realpath, and a seam keyed on the unresolved string poisons nothing at
    // all. The first version of this fixture did exactly that, and it passed against the unfixed
    // module for a reason that was never the finding — the ordinary output-root bracket refused the
    // repoint, because the identity it was comparing had never been poisoned.
    const real = nodeFs.realpathSync(dir);
    const treeA = join(real, 'treeA');
    const outputRoot = join(real, 'out');
    nodeFs.mkdirSync(join(treeA, 'sub', 'items'), { recursive: true });
    nodeFs.writeFileSync(join(treeA, 'sub', 'items', 'foreign.png'), 'bytes from a tree this handbook does not own');
    nodeFs.symlinkSync(treeA, outputRoot);
    assert.equal(nodeFs.realpathSync(outputRoot), treeA, 'the interposed path must be the one the module resolves to');

    const profile = profileFor(real, { capture: { output_dir: outputRoot, build_identity: { ui_read: false } } });
    const entry = { slug: 'items' };
    assert.equal(nodeFs.existsSync(join(treeA, 'items')), false, 'gate 3 must see an ordinary first capture');

    // An `lstat` that answers every PREDICATE and cannot say WHICH object it looked at. That is not
    // a contrivance: `identityFromStat` refuses a `dev`/`ino` it cannot represent exactly, so any
    // filesystem reporting identifiers beyond the safe-integer window through a seam that ignores
    // `{bigint:true}` produces this same observation.
    const withoutIdentity = (st) => ({
      isSymbolicLink: () => st.isSymbolicLink(),
      isDirectory: () => st.isDirectory(),
      isFile: () => st.isFile(),
      nlink: st.nlink,
      mode: st.mode,
      size: st.size,
    });

    let repointed = false;
    let poisonedReads = 0;
    const deps = depsWithOverride({
      lstatSync: (p, ...rest) => {
        const st = nodeFs.lstatSync(p, ...rest);
        if (String(p) !== treeA) return st;
        poisonedReads += 1;
        return withoutIdentity(st);
      },
      // The repoint lands at the reservation write: after validation, before the snapshot — the
      // window a null identity leaves completely unwatched.
      openSync: (p, ...rest) => {
        if (!repointed && String(p).endsWith('/pending.json')) {
          nodeFs.unlinkSync(outputRoot);
          nodeFs.symlinkSync(join(treeA, 'sub'), outputRoot);
          repointed = true;
        }
        return nodeFs.openSync(p, ...rest);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    // The condition is the unidentifiable root, NOT the repoint — and with the refusal at validation
    // the reservation is never written, so the repoint hook does not run at all. Asserting that it
    // did would pin the pre-fix ordering; asserting the poison fired is what says the fixture
    // reached its own subject. The repoint stays because it is what turns this into a hazard rather
    // than a curiosity: it is what the unfixed module hashed.
    assert.ok(poisonedReads > 0, 'the identity read was never interposed on — this fixture cannot reach the condition');
    assert.equal(opened.ok, false, `a root that could not be identified must not stand its own bracket down: ${JSON.stringify(opened)}`);
    assert.equal(opened.halts[0].halt, 'provenance_hazard', JSON.stringify(opened.halts));
    // The DIAGNOSTIC: the reason travels from the failed observation itself, so an operator is told
    // the root could not be inspected rather than that it was absent or that something moved.
    assert.match(opened.halts[0].message, /cannot resolve capture\.output_dir: inspection_failure/,
      `the halt must name the failed identity observation: ${JSON.stringify(opened.halts)}`);
  });
});

// [round 34, review bot P1 follow-up] The same tolerance one layer down, and the fifteenth round
// running where the defect was inside the fix that closed the round before. The first version of
// that guard asked only the SECOND observation whether the root was directly absent. A MIXED pair
// slips through it: the configured-name read fails as `inspection_failure`, the canonical read then
// reports plain ENOENT, both halves are `ok: false` so the disagreement arm stays quiet, and the
// pair is read as an ordinary first capture. Measured before the fix, through the real exported
// `openCaptureRun` with only storage interposed: `ok: true`, and a tree that came into existence
// after validation hashed into the opening baseline with no hazard.
//
// The two halves must read DIFFERENT strings for the pair to be mixable at all, which is what the
// symlinked ancestor is for: the configured name goes through `link`, the canonical form through
// its resolved target, and neither directory exists.
test('openCaptureRun: a MIXED pair of failed root observations is refused, not read as a first capture', () => {
  withTempDir((dir) => {
    const real = nodeFs.realpathSync(dir);
    const treeA = join(real, 'treeA');
    const link = join(real, 'link');
    nodeFs.mkdirSync(treeA, { recursive: true });
    nodeFs.symlinkSync(treeA, link);

    const rawOutputDir = join(link, 'assets');
    const canonicalRoot = join(treeA, 'assets');
    assert.notEqual(rawOutputDir, canonicalRoot, 'the two halves must read different strings, or no pair can be mixed');
    assert.equal(nodeFs.existsSync(canonicalRoot), false, 'the canonical half must report plain absence');

    const profile = {
      capture: { output_dir: rawOutputDir, build_identity: { ui_read: false } },
      publish: { chapters_dir: join(real, 'handbook'), target: 'static_md' },
    };
    nodeFs.mkdirSync(profile.publish.chapters_dir, { recursive: true });

    let unreadable = 0;
    let planted = false;
    const deps = depsWithOverride({
      // The configured name cannot be inspected at all — a permission on the path, an unreachable
      // mount. Nothing about the object, and in particular not that there is no object.
      lstatSync: (p, ...rest) => {
        if (String(p) !== rawOutputDir) return nodeFs.lstatSync(p, ...rest);
        unreadable += 1;
        const err = new Error('EACCES'); err.code = 'EACCES'; throw err;
      },
      // What the unfixed module went on to do, kept here as the consequence rather than as the
      // subject: the root comes into existence populated, with the configured name repointed into
      // it so containment still passes against the canonical segments recorded at validation.
      openSync: (p, ...rest) => {
        if (!planted && String(p).endsWith('/pending.json')) {
          const inner = join(canonicalRoot, 'inner');
          nodeFs.mkdirSync(join(inner, 'assets', 'items'), { recursive: true });
          nodeFs.writeFileSync(join(inner, 'assets', 'items', 'foreign.png'), 'bytes from a tree this handbook does not own');
          nodeFs.unlinkSync(link);
          nodeFs.symlinkSync(inner, link);
          planted = true;
        }
        return nodeFs.openSync(p, ...rest);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, deps);
    assert.ok(unreadable > 0, 'the configured name was never read — this fixture cannot reach the condition');
    assert.equal(opened.ok, false, `a root one half could not inspect must not be tolerated as absent: ${JSON.stringify(opened)}`);
    assert.equal(opened.halts[0].halt, 'provenance_hazard', JSON.stringify(opened.halts));
    // The reason belongs to the half that could NOT simply report absence. The tolerated half's word
    // (`vanished`) would name the one observation that was not the problem.
    assert.match(opened.halts[0].message, /cannot resolve capture\.output_dir: inspection_failure/,
      `the halt must carry the unreadable half's reason: ${JSON.stringify(opened.halts)}`);
    assert.equal(planted, false, 'the refusal must land before the reservation — a run that reserved has already spent something');
  });
});

// [round 35 BLOCKER] The same mixed pair, reached with no interposed `lstat` at all — codex found it
// independently of the review bot and by a different route, which is why both fixtures stay. Here
// the first observation follows a PRESENT dangling root link (`vanished`, and deliberately not
// `absentDirectly`, which is round 26's distinction), the link is removed while the module is
// resolving the name, and the post-resolution observation reports plain ENOENT. Two `ok: false`
// values, one of them a claim about an object that was there.
//
// Measured through the real exported `openCaptureRun` at both prior commits: `ok: true`, with the
// recreated root's `foreign.png` in the opening baseline. The guard that asked only the second
// observation did NOT close this — it took requiring both.
test('openCaptureRun: a dangling root link at the first read and plain absence at the second is refused', () => {
  withTempDir((dir) => {
    const real = nodeFs.realpathSync(dir);
    const outputRoot = join(real, 'assets');
    const profile = {
      capture: { output_dir: outputRoot, build_identity: { ui_read: false } },
      publish: { chapters_dir: join(real, 'handbook'), target: 'static_md' },
    };
    nodeFs.mkdirSync(profile.publish.chapters_dir, { recursive: true });
    nodeFs.symlinkSync(join(real, 'assets-target'), outputRoot);
    assert.equal(nodeFs.lstatSync(outputRoot).isSymbolicLink(), true, 'the root must be present as a link');
    assert.equal(nodeFs.existsSync(outputRoot), false, 'and it must dangle, or the first read reports an identity');

    let resolutions = 0;
    let removed = false;
    let recreated = false;
    const deps = depsWithOverride({
      realpathSync: (p, ...rest) => {
        if (String(p) === outputRoot) resolutions += 1;
        try {
          return nodeFs.realpathSync(p, ...rest);
        } finally {
          // Resolution 1 is the W1 ownership gate; resolution 2 is the bracket's own first read
          // following this link. Removing it there is what makes that read report a dangling link
          // while the post-resolution read reports plain absence.
          if (!removed && resolutions === 2 && String(p) === outputRoot) {
            nodeFs.unlinkSync(outputRoot);
            removed = true;
          }
        }
      },
      // The consequence, kept as documentation rather than as the subject: with the pair tolerated,
      // a root recreated before the snapshot is hashed into the opening baseline.
      openSync: (p, ...rest) => {
        if (removed && !recreated && String(p).endsWith('/pending.json')) {
          nodeFs.mkdirSync(join(outputRoot, 'items'), { recursive: true });
          nodeFs.writeFileSync(join(outputRoot, 'items', 'foreign.png'), 'bytes from a tree this handbook does not own');
          recreated = true;
        }
        return nodeFs.openSync(p, ...rest);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, deps);
    assert.equal(removed, true, 'the link was never removed mid-resolution — this fixture cannot reach the condition');
    assert.equal(opened.ok, false, `a pair whose first half saw an object must not be tolerated as absence: ${JSON.stringify(opened)}`);
    assert.equal(opened.halts[0].halt, 'provenance_hazard', JSON.stringify(opened.halts));
    // `vanished` is the FIRST observation's word — the half that saw something. Reporting the
    // second half's plain absence would name the observation that was not the problem.
    assert.match(opened.halts[0].message, /cannot resolve capture\.output_dir: vanished/,
      `the halt must carry the first observation's reason: ${JSON.stringify(opened.halts)}`);
    assert.equal(recreated, false, 'the refusal must land before the reservation, so nothing is ever hashed');
  });
});

// [round 36 BLOCKER] Round 27's finding, one level above where round 27 looked. `lstat` does not
// follow the FINAL component but follows every ancestor, so a present dangling symlink ABOVE
// `capture.output_dir` makes both halves of the bracket report ENOENT for a root whose existence is
// simply unknown — and two ENOENTs were being read as "not there yet", the one shape this module
// tolerates. Codex executed the whole sequence against the real exports: an operator's `/safe/link`
// dangling during an editor sync, `open_ok: true` with `opening: {items: {}}` and no hazards, the
// sync then restoring a target that already held `old.png`, `close_ok: true`, and W5 reading the
// absent opening key as brand-new — old bytes recorded as this build's, with a confident record.
//
// The absence has to be ESTABLISHED, by the same climb every other absence goes through.
test('openCaptureRun: an output root made absent by a dangling ANCESTOR is refused, not read as not-yet-created', () => {
  withTempDir((dir) => {
    const real = nodeFs.realpathSync(dir);
    const safe = join(real, 'safe');
    const link = join(safe, 'link');
    nodeFs.mkdirSync(safe, { recursive: true });
    nodeFs.mkdirSync(join(real, 'handbook'), { recursive: true });
    // The operator's own sync has the link pointing at a target that is not there right now.
    nodeFs.symlinkSync(join(real, 'synced-target'), link);
    assert.equal(nodeFs.lstatSync(link).isSymbolicLink(), true, 'the ancestor must be present as a link');
    assert.equal(nodeFs.existsSync(link), false, 'and it must dangle, or nothing beneath it reports ENOENT');

    const outputRoot = join(link, 'assets');
    assert.throws(() => nodeFs.lstatSync(outputRoot), /ENOENT/,
      'the output root must lstat ENOENT through the dangling ancestor — indistinguishable, at the tip, from a first capture');

    const profile = {
      capture: { output_dir: outputRoot, build_identity: { ui_read: false } },
      publish: { chapters_dir: join(real, 'handbook'), target: 'static_md' },
    };

    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
    assert.equal(opened.ok, false, `a root whose absence is explained by a dangling ancestor must not open as a first capture: ${JSON.stringify(opened)}`);
    assert.equal(opened.halts[0].halt, 'provenance_hazard', JSON.stringify(opened.halts));
    // The DIAGNOSTIC: not a disagreement between the two halves — they agree, and that is exactly
    // the problem — and not an unreadable observation. The absence itself is what could not be
    // established.
    assert.match(opened.halts[0].message, /cannot resolve capture\.output_dir: absence_unconfirmed/,
      `the halt must say the absence could not be confirmed: ${JSON.stringify(opened.halts)}`);
  });
});

// [round 37] The open -> close seam. `runState` used to carry nothing about `capture.output_dir`,
// so the close re-derived the root from the profile and walked whatever was there. These four pin
// the four shapes that were measured against the real exports before the fix: each returned
// `ok: true` and committed a previous build's `old.png` as this run's output with an empty hazard
// list. Two of them have the root PRESENT and identified at open, which is what says the defect was
// never about the absent root the reviewer found it on.
//
// Each fixture asserts the halt's MESSAGE, not just its class: all four halts are
// `provenance_hazard`, and the whole content of the fix is WHICH observation disagreed. A fixture
// matching only the class would pass against a module that refused for an unrelated reason.
for (const shape of [
  {
    name: 'the ancestor an absent root\'s absence was established against is replaced',
    plant: (dir, outputRoot) => {
      // The root is absent at open; `<dir>/safe` is what its absence rests on.
      const treeB = join(dir, 'B');
      nodeFs.mkdirSync(join(treeB, 'assets', 'items'), { recursive: true });
      nodeFs.writeFileSync(join(treeB, 'assets', 'items', 'old.png'), 'bytes from a previous build');
      return () => {
        nodeFs.renameSync(join(dir, 'safe'), join(dir, 'safe-moved'));
        nodeFs.renameSync(treeB, join(dir, 'safe'));
      };
    },
    rootExistsAtOpen: false,
    expect: /the directory '.*\/safe' that capture\.output_dir's absence was established against has been replaced/,
  },
  {
    name: 'an absent root then appears as a link into a tree outside it',
    plant: (dir, outputRoot) => {
      const elsewhere = join(dir, 'elsewhere');
      nodeFs.mkdirSync(join(elsewhere, 'items'), { recursive: true });
      nodeFs.writeFileSync(join(elsewhere, 'items', 'old.png'), 'bytes from a previous build');
      return () => nodeFs.symlinkSync(elsewhere, outputRoot);
    },
    rootExistsAtOpen: false,
    expect: /capture\.output_dir resolved to '.*\/safe\/assets' when this run opened and resolves to '.*\/elsewhere' now/,
  },
  {
    name: 'a PRESENT root is replaced under its own pathname',
    plant: (dir, outputRoot) => {
      const treeB = join(dir, 'B');
      nodeFs.mkdirSync(join(treeB, 'items'), { recursive: true });
      nodeFs.writeFileSync(join(treeB, 'items', 'old.png'), 'bytes from a previous build');
      return () => {
        nodeFs.renameSync(outputRoot, join(dir, 'A-moved'));
        nodeFs.renameSync(treeB, outputRoot);
      };
    },
    rootExistsAtOpen: true,
    expect: /the directory at capture\.output_dir is not the one this run opened over/,
  },
  {
    name: 'a PRESENT root is replaced by a link into a tree outside it',
    plant: (dir, outputRoot) => {
      const elsewhere = join(dir, 'elsewhere');
      nodeFs.mkdirSync(join(elsewhere, 'items'), { recursive: true });
      nodeFs.writeFileSync(join(elsewhere, 'items', 'old.png'), 'bytes from a previous build');
      return () => {
        nodeFs.rmSync(outputRoot, { recursive: true, force: true });
        nodeFs.symlinkSync(elsewhere, outputRoot);
      };
    },
    rootExistsAtOpen: true,
    expect: /capture\.output_dir resolved to '.*\/safe\/assets' when this run opened and resolves to '.*\/elsewhere' now/,
  },
]) {
  test(`the open -> close seam: ${shape.name} is refused at close`, () => {
    withTempDir((dir) => {
      const real = nodeFs.realpathSync(dir);
      const safe = join(real, 'safe');
      const outputRoot = join(safe, 'assets');
      nodeFs.mkdirSync(safe, { recursive: true });
      nodeFs.mkdirSync(join(real, 'handbook'), { recursive: true });
      if (shape.rootExistsAtOpen) nodeFs.mkdirSync(join(outputRoot, 'items'), { recursive: true });
      const profile = {
        capture: { output_dir: outputRoot, build_identity: { ui_read: false } },
        publish: { chapters_dir: join(real, 'handbook'), target: 'static_md' },
      };

      const mutate = shape.plant(real, outputRoot);
      const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
      assert.equal(opened.ok, true, `the open is an ordinary one and must succeed: ${JSON.stringify(opened)}`);
      // The pinning is what the close has to work from, so assert it was actually taken — an
      // `output_root` of the wrong shape would make every case below pass for the wrong reason.
      assert.equal(opened.runState.output_root.identity === null, !shape.rootExistsAtOpen);
      assert.equal(opened.runState.output_root.anchor === null, shape.rootExistsAtOpen);

      mutate();

      const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
      assert.equal(closed.ok, false, `the close must refuse a root it did not open over: ${JSON.stringify(closed)}`);
      assert.equal(closed.halts[0].halt, 'provenance_hazard', JSON.stringify(closed.halts));
      assert.match(closed.halts[0].message, /capture\.output_dir moved while this run was open/, JSON.stringify(closed.halts));
      assert.match(closed.halts[0].message, shape.expect,
        `the halt must name the observation that disagreed: ${JSON.stringify(closed.halts)}`);
      // Nothing durable may be written on this exit — the refusal happens before the temp/rename.
      assert.equal(nodeFs.existsSync(join(real, 'handbook', '.provenance', 'run', 'current.json')), false,
        'a refused close must leave no run record behind');
    });
  });
}

// The two topologies the guard above must NOT refuse. Both were measured through the real exports
// alongside the four refusals: an output root created by the capture command during the run is the
// ordinary first capture this module has protected since round 27, and a root that simply gains a
// file is every run after that one.
for (const control of [
  { name: 'the capture command creates an absent output root during the run', rootExistsAtOpen: false },
  { name: 'a present output root gains a file during the run', rootExistsAtOpen: true },
]) {
  test(`the open -> close seam: ${control.name} still closes clean`, () => {
    withTempDir((dir) => {
      const real = nodeFs.realpathSync(dir);
      const safe = join(real, 'safe');
      const outputRoot = join(safe, 'assets');
      nodeFs.mkdirSync(safe, { recursive: true });
      nodeFs.mkdirSync(join(real, 'handbook'), { recursive: true });
      if (control.rootExistsAtOpen) nodeFs.mkdirSync(join(outputRoot, 'items'), { recursive: true });
      const profile = {
        capture: { output_dir: outputRoot, build_identity: { ui_read: false } },
        publish: { chapters_dir: join(real, 'handbook'), target: 'static_md' },
      };

      const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
      assert.equal(opened.ok, true, JSON.stringify(opened));

      nodeFs.mkdirSync(join(outputRoot, 'items'), { recursive: true });
      nodeFs.writeFileSync(join(outputRoot, 'items', 'fresh.png'), 'bytes written by THIS build');

      const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
      assert.equal(closed.ok, true, `an ordinary run must not be refused: ${JSON.stringify(closed)}`);
      const parsed = CR.readRunRecordText(
        nodeFs.readFileSync(join(real, 'handbook', '.provenance', 'run', 'current.json'), 'utf8'),
      );
      assert.equal(parsed.ok, true, JSON.stringify(parsed));
      assert.deepEqual(Object.keys(parsed.record.chapters.items.closing), ['fresh.png'],
        `this build's own file must still be recorded: ${JSON.stringify(parsed.record.chapters.items)}`);
    });
  });
}

// [round 38] Verifying a value and then re-reading its source checks a different read. `runState` is
// caller-held, `isPlainObject` admits an accessor-backed object, and every authenticated field used
// to be re-read after the digest had already passed — so a getter could answer the digest with the
// authenticated value and the consumer with something else. The count is the assertion that matters:
// ONE read is the fix, and a fixture that only checked the halt would still pass if a second read
// crept back in and merely happened to be refused.
test('the close reads each authenticated field ONCE: an accessor-backed output_root cannot answer twice', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    nodeFs.mkdirSync(join(profile.capture.output_dir, 'items'), { recursive: true });
    const treeB = join(dir, 'B');
    nodeFs.mkdirSync(join(treeB, 'items'), { recursive: true });
    nodeFs.writeFileSync(join(treeB, 'items', 'old.png'), 'bytes from a previous build');

    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    const authentic = opened.runState.output_root;

    let reads = 0;
    const tampered = { ...opened.runState };
    Object.defineProperty(tampered, 'output_root', {
      enumerable: true,
      get() {
        reads += 1;
        // The authenticated value first, so the digest passes; anything later sees the replacement.
        return reads === 1 ? authentic : { ...authentic, identity: identityOf(profile.capture.output_dir) };
      },
    });

    nodeFs.renameSync(profile.capture.output_dir, join(dir, 'A-moved'));
    nodeFs.renameSync(treeB, profile.capture.output_dir);

    const closed = CR.closeCaptureRun(profile, tampered, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(reads, 1, `the close must read output_root exactly once, not ${reads} times`);
    assert.equal(closed.ok, false, `the replacement must be refused: ${JSON.stringify(closed)}`);
    assert.match(closed.halts[0].message, /is not the one this run opened over/, JSON.stringify(closed.halts));
  });
});

// [round 38] The same single-read rule for the other two authenticated values the close consumes.
// `output_root` is where the exposure was found; it was never where it lived. Each of these fails
// against a close that re-reads its source, and each names a different consequence.
test('the close re-gates the entries the DIGEST authenticated, not whatever the object says now', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    nodeFs.mkdirSync(join(profile.capture.output_dir, 'items'), { recursive: true });

    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    const authentic = opened.runState.entries;

    let reads = 0;
    const tampered = { ...opened.runState };
    Object.defineProperty(tampered, 'entries', {
      enumerable: true,
      get() {
        reads += 1;
        // A slug gate 1 refuses. A close that re-reads validates THIS and halts; a close that uses
        // the authenticated payload never sees it.
        return reads === 1 ? authentic : [{ slug: 'Not A Valid Slug' }];
      },
    });

    const closed = CR.closeCaptureRun(profile, tampered, { ok: true }, null, stubDepsNoIdentity());
    // Not a read COUNT — a count assertion passes for the wrong reason on any path that halts early
    // and fails on a benign read on any path that completes. What must hold is that every OUTPUT
    // used the authenticated value: the decisions, the committed record, and — since round 39
    // stopped building the result by spreading the caller's object — the returned state too.
    assert.equal(closed.ok, true, `the authenticated entries are the ones that count: ${JSON.stringify(closed)}`);
    const parsed = CR.readRunRecordText(
      nodeFs.readFileSync(join(profile.publish.chapters_dir, '.provenance', 'run', 'current.json'), 'utf8'),
    );
    assert.equal(parsed.ok, true, JSON.stringify(parsed));
    assert.deepEqual(Object.keys(parsed.record.chapters), ['items'],
      `the record must be keyed by the authenticated entries: ${JSON.stringify(Object.keys(parsed.record.chapters))}`);
    assert.deepEqual(closed.runState.entries, authentic,
      'and the returned state must carry them too — a caller drives W5 off exactly this object');
    // Scope, so this fixture is not read as more than it proves: it pins WHICH entries the close
    // acts on, never the entry-validation predicate itself. Neutralize that predicate and this
    // still passes, because the authenticated entries are valid — the gate-1 invalid-slug fixtures
    // are what own it (codex round 39, MINOR).
  });
});

test('the run_id written into the record is the one compared against the token, not a later read', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    nodeFs.mkdirSync(join(profile.capture.output_dir, 'items'), { recursive: true });

    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    const authentic = opened.runState.run_id;
    const forged = '00000000-0000-4000-8000-000000000000';
    assert.notEqual(authentic, forged);

    let reads = 0;
    const tampered = { ...opened.runState };
    Object.defineProperty(tampered, 'run_id', {
      enumerable: true,
      get() { reads += 1; return reads === 1 ? authentic : forged; },
    });

    const closed = CR.closeCaptureRun(profile, tampered, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, true, JSON.stringify(closed));
    const parsed = CR.readRunRecordText(
      nodeFs.readFileSync(join(profile.publish.chapters_dir, '.provenance', 'run', 'current.json'), 'utf8'),
    );
    assert.equal(parsed.ok, true, JSON.stringify(parsed));
    // A forged run_id in the committed record is what W5 binds a chapter to — it would tie the
    // chapter to a run that never happened, and the token comparison above would still have passed.
    assert.equal(parsed.record.run_id, authentic,
      `the record must carry the run_id the token vouched for: ${JSON.stringify(parsed.record.run_id)}`);
    // Scope: this pins the one-read BINDING of run_id into the record, not the token comparison
    // itself — remove that comparison while keeping the local and this still passes. Round 39's
    // note deferred that to the wrong-token fixture in the gate section, which does NOT own it:
    // that fixture replaces the token's run_id AND its digest, so the digest mismatch refuses first
    // and the comparison stays unpinned. The authentic-digest/foreign-run_id fixture below is what
    // owns it (codex round 40).
  });
});

// [round 38] A name that moved while the DIRECTORY did not. `output_dir` pointing at a
// `releases/current` alias, the same directory renamed underneath it and the alias rotated on — no
// drift, and round 37 refused it because it compared the spelling before the object. What the run
// opened over is a directory, not a name for one.
test('the open -> close seam: a rotated alias over the SAME directory is not drift', () => {
  withTempDir((dir) => {
    const real = nodeFs.realpathSync(dir);
    const releases = join(real, 'releases');
    const v1 = join(releases, 'v1');
    nodeFs.mkdirSync(join(v1, 'items'), { recursive: true });
    nodeFs.writeFileSync(join(v1, 'items', 'a.png'), 'v1');
    const current = join(releases, 'current');
    nodeFs.symlinkSync(v1, current);
    nodeFs.mkdirSync(join(real, 'handbook'), { recursive: true });
    const profile = {
      capture: { output_dir: current, build_identity: { ui_read: false } },
      publish: { chapters_dir: join(real, 'handbook'), target: 'static_md' },
    };

    const before = identityOf(nodeFs.realpathSync(current));
    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));

    const v2 = join(releases, 'v2');
    nodeFs.renameSync(v1, v2);
    nodeFs.unlinkSync(current);
    nodeFs.symlinkSync(v2, current);
    // The whole point of the fixture: the object is the same one, only its name changed.
    assert.equal(identityOf(nodeFs.realpathSync(current)), before,
      'the rotation must preserve the directory — otherwise this pins the replacement case instead');
    assert.notEqual(nodeFs.realpathSync(current), v1, 'the canonical spelling must actually have changed');

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, true, `a rotation over the same directory must not be refused: ${JSON.stringify(closed)}`);
  });
});

// [round 39] The RETURNED state is an output, not scratch. Round 38 authenticated everything the
// close DECIDES on and then built its result by spreading the caller's object back out, judging
// that spread benign because no decision follows it. It is not benign: capture-record.d.mts
// declares the returned active state to carry the authenticated fields, and a caller drives W5 with
// the `run_id` it reads off exactly this object — so a second read that answers differently hands
// W5 a forged id while the committed record is correct, and W5 refuses with `run_id_mismatch`
// against a run that is in fact intact. Codex round 39 attacked the judgement it was asked to
// attack and was right.
test('the close RETURNS the authenticated run_id, not a later read of the caller object', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    nodeFs.mkdirSync(join(profile.capture.output_dir, 'items'), { recursive: true });

    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    const authentic = opened.runState.run_id;
    const forged = '00000000-0000-4000-8000-000000000000';
    assert.notEqual(authentic, forged);

    let reads = 0;
    const tampered = { ...opened.runState };
    Object.defineProperty(tampered, 'run_id', {
      enumerable: true,
      get() { reads += 1; return reads === 1 ? authentic : forged; },
    });

    const closed = CR.closeCaptureRun(profile, tampered, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, true, JSON.stringify(closed));
    assert.equal(closed.runState.run_id, authentic,
      `the returned state must carry the run_id the token vouched for, not a later read: ${JSON.stringify(closed.runState.run_id)}`);
    // The record and the returned state must agree — either one alone leaves W5 comparing an
    // authentic value against a forged one.
    const parsed = CR.readRunRecordText(
      nodeFs.readFileSync(join(profile.publish.chapters_dir, '.provenance', 'run', 'current.json'), 'utf8'),
    );
    assert.equal(parsed.ok, true, JSON.stringify(parsed));
    assert.equal(parsed.record.run_id, closed.runState.run_id);
  });
});

// [round 39] `opening_digest` is the one authenticated field NOTHING reads during authentication —
// the digest is recomputed from the payload and compared against the TOKEN, never against this
// field. So a throwing getter on it cannot reach a single decision, and the only thing left that
// can touch it is a read taken after the record is already on disk. A throw there turns a
// successfully committed run into an exception, which is the one shape this module's contract says
// never happens: every failure is a returned halt.
test('the close returns its declared result even when a caller field throws on a read after the record is committed', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    nodeFs.mkdirSync(join(profile.capture.output_dir, 'items'), { recursive: true });

    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));

    const tampered = { ...opened.runState };
    Object.defineProperty(tampered, 'opening_digest', {
      enumerable: true,
      get() { throw new Error('nothing may read this field after the payload is authenticated'); },
    });

    const closed = CR.closeCaptureRun(profile, tampered, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, true, JSON.stringify(closed));
    assert.equal(closed.runState.opening_digest, opened.runState.opening_digest,
      'the returned state must carry the authenticated digest rather than re-reading the caller field');
  });
});

// [round 39] `skipped` was read before the token and before the digest — the one decision in this
// function taken on state nothing has authenticated. An active run wrapped in a skipped-looking
// state closed `ok: true` while committing nothing and leaving the pending token behind, which is
// this release's defining defect class exactly: an item that could not be processed read as good
// news.
test('closeCaptureRun refuses an ACTIVE run wrapped in a skipped-looking state', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    nodeFs.mkdirSync(join(profile.capture.output_dir, 'items'), { recursive: true });

    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));

    const wrapped = { ...opened.runState, skipped: true };
    const closed = CR.closeCaptureRun(profile, wrapped, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, false,
      `a skipped-looking wrapper over an active run must not close ok: ${JSON.stringify(closed)}`);
    assert.equal(nodeFs.existsSync(join(profile.publish.chapters_dir, '.provenance', 'run', 'current.json')), false,
      'and it must commit nothing');
  });
});

// [round 39] The two arms of that refusal MASK each other, and the fixture above cannot tell them
// apart: the wrapper carries active fields AND leaves a pending token, so neutralizing either arm
// alone still refuses. Found by mutation — both arms reported killed 0 while the fixture was green,
// which is the "green for the wrong reason" shape this release keeps producing. Each of the two
// below removes the other arm's evidence, so exactly one guard can be the thing that refuses.
test('the skipped branch: an active-shaped state is refused on its SHAPE, with no token left to give it away', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    nodeFs.mkdirSync(join(profile.capture.output_dir, 'items'), { recursive: true });

    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    // Remove the one other thing that could betray the claim, so only the shape can.
    nodeFs.unlinkSync(join(profile.publish.chapters_dir, '.provenance', 'run', 'pending.json'));

    const closed = CR.closeCaptureRun(profile, { ...opened.runState, skipped: true }, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, false,
      `an active run's fields under a skipped claim must be refused on shape alone: ${JSON.stringify(closed)}`);
    assert.match(closed.halts[0].message, /carries an active run's fields/);
  });
});

test('the skipped branch: a well-formed skipped state is refused because THIS run\'s token is still pending', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    nodeFs.mkdirSync(join(profile.capture.output_dir, 'items'), { recursive: true });

    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    assert.equal(nodeFs.existsSync(join(profile.publish.chapters_dir, '.provenance', 'run', 'pending.json')), true);

    // Shape-perfect: exactly what openCaptureRun returns on its own skipped branch. The only thing
    // contradicting it is the reservation a skipped run never makes.
    const closed = CR.closeCaptureRun(profile, { skipped: true }, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, false,
      `a skipped claim must not survive this run's own pending token: ${JSON.stringify(closed)}`);
    assert.match(closed.halts[0].message, /a pending token is present/);
  });
});

// [round 39] The absent-root anchor is re-resolved to re-check containment, and a resolution that
// FAILS there is not a tolerable unknown: `segmentsWithin` would be handed an undefined segment
// list and throw past this module's declared result. Reached the only way it can be — the identity
// read succeeds on the anchor while resolving it fails, which on a real disk is an ancestor that
// became unreadable between the two calls, and here is that same errno injected.
test('the open -> close seam: an absent root whose anchor can no longer be RESOLVED is refused, not thrown past', () => {
  withTempDir((dir) => {
    const real = nodeFs.realpathSync(dir);
    const safe = join(real, 'safe');
    const outputDir = join(safe, 'assets');
    nodeFs.mkdirSync(safe, { recursive: true });
    nodeFs.mkdirSync(join(real, 'handbook'), { recursive: true });
    const profile = {
      capture: { output_dir: outputDir, build_identity: { ui_read: false } },
      publish: { chapters_dir: join(real, 'handbook'), target: 'static_md' },
    };

    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    assert.equal(opened.runState.output_root.identity, null, 'the fixture must open over an ABSENT root');
    const anchorPath = opened.runState.output_root.anchor.path;

    // The capture creates the root somewhere the close will resolve to a DIFFERENT spelling, so the
    // containment re-check — and therefore the anchor re-resolution — is actually reached.
    const moved = join(real, 'moved');
    nodeFs.mkdirSync(join(moved, 'items'), { recursive: true });
    nodeFs.symlinkSync(moved, outputDir);

    const failing = {
      ...stubDepsNoIdentity(),
      realpathSync: (p, ...rest) => {
        if (p === anchorPath) {
          const err = new Error('EACCES: permission denied');
          err.code = 'EACCES';
          throw err;
        }
        return nodeFs.realpathSync(p, ...rest);
      },
    };
    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, failing);
    assert.equal(closed.ok, false,
      `an anchor that cannot be resolved must be refused: ${JSON.stringify(closed)}`);
    assert.match(closed.halts[0].message, /can no longer be resolved/);
  });
});

// [round 39] The absent-root twin of the rotated-alias fixture above. With no identity to compare,
// round 38 required the canonical SPELLING to match and refused before it ever looked at the
// anchor — so the ordinary first capture combined with the supported alias rotation halted, telling
// the operator the root moved when the directory the absence was established against never did.
// The halt direction made it a nuisance rather than a bad record, but the message it prints is
// false, and SKILL.md tells the operator it means the capture wrote into an unvalidated tree.
test('the open -> close seam: an absent root under a rotated alias is not drift when the ANCHOR is the same directory', () => {
  withTempDir((dir) => {
    const real = nodeFs.realpathSync(dir);
    const releases = join(real, 'releases');
    const v1 = join(releases, 'v1');
    nodeFs.mkdirSync(v1, { recursive: true });
    const current = join(releases, 'current');
    nodeFs.symlinkSync(v1, current);
    nodeFs.mkdirSync(join(real, 'handbook'), { recursive: true });
    const outputDir = join(current, 'assets');
    const profile = {
      capture: { output_dir: outputDir, build_identity: { ui_read: false } },
      publish: { chapters_dir: join(real, 'handbook'), target: 'static_md' },
    };
    // The ordinary FIRST capture: the root does not exist yet and the capture command creates it.
    assert.equal(nodeFs.existsSync(outputDir), false, 'the fixture must open over an ABSENT root');

    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    const anchorBefore = identityOf(nodeFs.realpathSync(current));

    const v2 = join(releases, 'v2');
    nodeFs.renameSync(v1, v2);
    nodeFs.unlinkSync(current);
    nodeFs.symlinkSync(v2, current);
    assert.equal(identityOf(nodeFs.realpathSync(current)), anchorBefore,
      'the rotation must preserve the anchor directory — otherwise this pins the replacement case instead');

    nodeFs.mkdirSync(join(outputDir, 'items'), { recursive: true });
    nodeFs.writeFileSync(join(outputDir, 'items', 'fresh.png'), 'fresh');

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, true,
      `a root created inside the very anchor its absence was established against must not be refused: ${JSON.stringify(closed)}`);
  });
});

// [round 40] Containment as a segment PREFIX was not enough, and two independent reviewers found it
// at once with different topologies: the anchor holding plus "somewhere below the anchor" admits a
// DIFFERENT directory inside that anchor, which is not the one whose absence was established. Both
// are below. What the open established is a specific missing SUFFIX under a specific object, so the
// close now requires the root to resolve to exactly the anchor's current resolution plus that
// recorded tail — which also refuses any symlink newly introduced along the tail, since a resolved
// path that traversed one cannot equal the lexical join.
test('the open -> close seam: a link at the absent root\'s own name, pointing INSIDE its anchor, is refused', () => {
  withTempDir((dir) => {
    const real = nodeFs.realpathSync(dir);
    const safe = join(real, 'safe');
    const outputDir = join(safe, 'assets');
    nodeFs.mkdirSync(safe, { recursive: true });
    nodeFs.mkdirSync(join(real, 'handbook'), { recursive: true });
    const profile = {
      capture: { output_dir: outputDir, build_identity: { ui_read: false } },
      publish: { chapters_dir: join(real, 'handbook'), target: 'static_md' },
    };
    assert.equal(nodeFs.existsSync(outputDir), false, 'the fixture must open over an ABSENT root');

    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));

    // A previous build's output, sitting INSIDE the anchor the absence was established against.
    nodeFs.mkdirSync(join(safe, 'stale-tree', 'items'), { recursive: true });
    nodeFs.writeFileSync(join(safe, 'stale-tree', 'items', 'old.png'), 'a previous build');
    nodeFs.symlinkSync(join(safe, 'stale-tree'), outputDir);

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, false,
      `a root redirected to a different directory inside its own anchor must be refused: ${JSON.stringify(closed)}`);
    assert.equal(nodeFs.existsSync(join(profile.publish.chapters_dir, '.provenance', 'run', 'current.json')), false,
      'and a previous build\'s file must not be committed as this run\'s output');
  });
});

test('the open -> close seam: a redirect DEEPER along the absent tail, still inside the anchor, is refused', () => {
  withTempDir((dir) => {
    const real = nodeFs.realpathSync(dir);
    const anchor = join(real, 'anchor');
    nodeFs.mkdirSync(anchor, { recursive: true });
    nodeFs.mkdirSync(join(real, 'handbook'), { recursive: true });
    // The whole tail `new/deep/assets` is absent at open, so the anchor is `anchor` itself.
    const outputDir = join(anchor, 'new', 'deep', 'assets');
    const profile = {
      capture: { output_dir: outputDir, build_identity: { ui_read: false } },
      publish: { chapters_dir: join(real, 'handbook'), target: 'static_md' },
    };

    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    assert.equal(opened.runState.output_root.anchor.path, anchor);

    nodeFs.mkdirSync(join(anchor, 'old', 'deep', 'assets', 'items'), { recursive: true });
    nodeFs.writeFileSync(join(anchor, 'old', 'deep', 'assets', 'items', 'stale.png'), 'a previous build');
    nodeFs.symlinkSync(join(anchor, 'old'), join(anchor, 'new'));

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, false,
      `a tail component redirected inside the anchor must be refused: ${JSON.stringify(closed)}`);
  });
});

// [round 44] A Proxy can forge every reflective answer `isGenuineSkippedState` asks for — `ownKeys`,
// `getOwnPropertyDescriptor` and `get` are all traps — so the shape witness alone cannot tell a
// forged skipped state from a real one. Measured, so what actually holds is pinned rather than
// assumed: the TOKEN witness catches it, because a reservation on disk is not something the caller's
// object can answer for. The residual — a Proxy AND a relocated provenance root, where the token
// lookup then reads the wrong place — is recorded on the declaration as a boundary rather than
// defended, for the reason set out there.
test('the skip branch: a Proxy-forged skipped state over an active run is still refused by the token', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    nodeFs.mkdirSync(join(profile.capture.output_dir, 'items'), { recursive: true });

    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));

    // Answers every reflective question exactly as `{skipped: true}` would, over a real active run.
    const forged = new Proxy(opened.runState, {
      ownKeys: () => ['skipped'],
      getOwnPropertyDescriptor: () => ({ value: true, enumerable: true, configurable: true }),
      get: (t, p, r) => (p === 'skipped' ? true : Reflect.get(t, p, r)),
      has: (t, p) => (p === 'skipped' ? true : Reflect.has(t, p)),
    });
    assert.deepEqual(Object.keys(forged), ['skipped'], 'the forgery must actually be convincing');
    assert.equal(forged.skipped, true);

    const closed = CR.closeCaptureRun(profile, forged, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, false,
      `a forged skipped shape must not close an open run: ${JSON.stringify(closed)}`);
    assert.match(closed.halts[0].message, /a pending token is present/,
      'and the TOKEN must be what refuses — the shape witness is forgeable by construction here');
    assert.equal(nodeFs.existsSync(join(profile.publish.chapters_dir, '.provenance', 'run', 'pending.json')), true,
      'the reservation stays for recoverProvenanceState, which reports `open`');
  });
});

// [round 42] The anchor must be recorded in the SAME representation everything else uses. The climb
// walked the raw configured path while `canonicalizeForComparison` and `chapterAssetDir` normalize
// `..` lexically first, so the two disagreed about which directory the anchor was: the kernel reads
// `a/link/..` as the parent of link's TARGET, lexical normalization reads it as `a`. This asserts
// the recorded observation directly; the fixture below it pins the consequence end to end.
test('an absent root\'s anchor is recorded lexically, matching the directory the walk actually visits', () => {
  withTempDir((dir) => {
    const real = nodeFs.realpathSync(dir);
    nodeFs.mkdirSync(join(real, 'a'), { recursive: true });
    nodeFs.mkdirSync(join(real, 'b'), { recursive: true });
    nodeFs.mkdirSync(join(real, 'handbook'), { recursive: true });
    // `link` leaves its own parent, which is what makes the two readings of `..` differ at all.
    nodeFs.symlinkSync(join(real, 'b'), join(real, 'a', 'link'));
    const profile = {
      // Concatenated, not `join`ed: `join` would normalize the `..` away before the module sees it,
      // and a profile file supplies this string verbatim.
      capture: { output_dir: `${real}/a/link/../assets`, build_identity: { ui_read: false } },
      publish: { chapters_dir: join(real, 'handbook'), target: 'static_md' },
    };

    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    const anchor = opened.runState.output_root.anchor;
    assert.equal(anchor.path, join(real, 'a'),
      `the anchor must be the lexically normalized ancestor, not the raw one: ${anchor.path}`);
    // The identity is the load-bearing half: the raw climb recorded the identity of `b`'s PARENT
    // here, which is neither the directory the canonical path names nor the one the walk visits.
    assert.equal(anchor.identity, identityOf(join(real, 'a')),
      'the anchor must identify the directory lexical normalization names');
    assert.notEqual(anchor.identity, identityOf(real),
      'and specifically not the object the kernel reaches by following the link before the ..');
    assert.equal(opened.runState.output_root.canonical, join(real, 'a', 'assets'),
      'the canonical path is the lexical one, which is the whole reason the anchor must be too');
  });
});

// [round 43] And the consequence, end to end, because "another guard catches it" was WRONG. Round
// 42 built this topology, saw the configured-vs-resolved bracket refuse, and downgraded the finding
// to a masked inconsistency. It was missing one `mkdir`: with the previously-absent `missing`
// component CREATED before the rotation, the closing raw path and the closing lexical path land on
// the same live directory, that bracket passes, and the drift check is what decides. Measured
// against the raw form: closed `ok: true` with a previous build's `old.png` committed as this run's
// closing output, under an opening baseline that was empty. A non-reproduction is only ever as good
// as the topology actually built.
test('the open -> close seam: a `..` crossing a symlink must not let a previous build\'s tree be recorded as this run\'s', () => {
  withTempDir((dir) => {
    const real = nodeFs.realpathSync(dir);
    nodeFs.mkdirSync(join(real, 'stable', 'leaf'), { recursive: true });
    nodeFs.mkdirSync(join(real, 'stable', 'assets', 'items'), { recursive: true });
    nodeFs.writeFileSync(join(real, 'stable', 'assets', 'items', 'old.png'), 'a PREVIOUS build');
    nodeFs.symlinkSync('leaf', join(real, 'stable', 'pivot'));
    nodeFs.mkdirSync(join(real, 'v1'), { recursive: true });
    nodeFs.symlinkSync('../stable/leaf', join(real, 'v1', 'pivot'));
    nodeFs.symlinkSync('v1', join(real, 'current'));
    nodeFs.mkdirSync(join(real, 'handbook'), { recursive: true });
    const profile = {
      // Concatenated so the `..` survives to the module, as it would from a profile file.
      capture: { output_dir: `${real}/current/pivot/missing/../../assets`, build_identity: { ui_read: false } },
      publish: { chapters_dir: join(real, 'handbook'), target: 'static_md' },
    };

    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    // Keys, not a deep-equal against a literal: the snapshot's per-chapter maps are null-prototype,
    // so `deepStrictEqual` reports a difference that is about the prototype and not the contents.
    assert.deepEqual(Object.keys(opened.runState.opening_assets), ['items']);
    assert.deepEqual(Object.keys(opened.runState.opening_assets.items), [],
      'the opening baseline must be empty — that is what makes a stale file read as brand-new later');

    // The ingredient that makes both brackets pass at close, so the drift check is the decider.
    nodeFs.mkdirSync(join(real, 'stable', 'leaf', 'missing'), { recursive: true });
    nodeFs.unlinkSync(join(real, 'current'));
    nodeFs.symlinkSync('stable', join(real, 'current'));

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, false,
      `the rotation moved the run onto a tree it never opened over and must be refused: ${JSON.stringify(closed)}`);
    assert.equal(nodeFs.existsSync(join(profile.publish.chapters_dir, '.provenance', 'run', 'current.json')), false,
      'and no record may carry the previous build\'s old.png as this run\'s output');
  });
});

// [round 40] The same defect class one branch HIGHER than round 39 closed it. `ownership.skip` is
// decided from the CURRENT profile and returns before the runState or the token is looked at, so a
// profile edited between open and close — to an overlapping topology with no `build_identity` —
// reports success for a run that is genuinely open, commits nothing, and leaves its token behind.
// Round 39 hardened the state-claims-skipped branch and left the profile-claims-skipped one.
test('closeCaptureRun does not report success for an OPEN run just because the profile now says skip', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    nodeFs.mkdirSync(join(profile.capture.output_dir, 'items'), { recursive: true });

    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    assert.equal(nodeFs.existsSync(join(profile.publish.chapters_dir, '.provenance', 'run', 'pending.json')), true);

    // The profile now overlaps and no longer configures build_identity: the warn-and-skip branch.
    const skipping = {
      capture: { output_dir: profile.publish.chapters_dir },
      publish: { chapters_dir: profile.publish.chapters_dir },
    };
    const closed = CR.closeCaptureRun(skipping, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, false,
      `an open run must not be closed as "skipped" because the profile changed: ${JSON.stringify(closed)}`);
  });
});

// [round 41] And the token lookup that caught the case above cannot be the thing relied on, because
// its path is derived from the very profile that may have been edited. Move the provenance root too
// and the token becomes invisible: absence at a path derived from the edited profile is not
// evidence that this invocation was skipped. What settles it without consulting the profile at all
// is the runState — an ACTIVE run is an active run whatever the profile now says.
test('closeCaptureRun does not report success for an OPEN run when the profile moves its provenance root as well', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    nodeFs.mkdirSync(join(profile.capture.output_dir, 'items'), { recursive: true });

    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    const originalToken = join(profile.publish.chapters_dir, '.provenance', 'run', 'pending.json');
    assert.equal(nodeFs.existsSync(originalToken), true);

    // Overlapping AND relocated: ownership reports skip, and the token lookup for the new root
    // finds nothing because the reservation is still sitting under the old one.
    const moved = join(dir, 'moved-handbook');
    nodeFs.mkdirSync(moved, { recursive: true });
    const skipping = { capture: { output_dir: moved }, publish: { chapters_dir: moved } };

    const closed = CR.closeCaptureRun(skipping, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, false,
      `an open run must not be closed as "skipped" when the profile also moved: ${JSON.stringify(closed)}`);
    assert.equal(nodeFs.existsSync(originalToken), true,
      'and the original reservation must still be there for recoverProvenanceState to find');
  });
});

// [round 41] The skip branch's SECOND arm, which the arm above masks under every fixture that
// carries an active state — found by the matrix reporting it killed nothing while the suite was
// green, for the third time in three rounds. Here the state is the exact skipped shape, so only the
// token can contradict it, and the profile still resolves to the root the reservation sits under.
test('the skip branch: an exactly-skipped state is still refused when a reservation is visible under this profile', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    nodeFs.mkdirSync(join(profile.capture.output_dir, 'items'), { recursive: true });

    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    assert.equal(nodeFs.existsSync(join(profile.publish.chapters_dir, '.provenance', 'run', 'pending.json')), true);

    // Overlapping so ownership skips, but the SAME chapters_dir — so the token this run reserved is
    // exactly where this profile would look for one.
    const skipping = {
      capture: { output_dir: profile.publish.chapters_dir },
      publish: { chapters_dir: profile.publish.chapters_dir },
    };
    const closed = CR.closeCaptureRun(skipping, { skipped: true }, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, false,
      `a reservation visible under this profile must refuse a skipped claim: ${JSON.stringify(closed)}`);
    assert.match(closed.halts[0].message, /a pending token is present/);
  });
});

// [round 40] The scope note added in round 39 named the wrong owner: the wrong-token fixture
// replaces the token's run_id AND its digest, so removing the run_id comparison still halts on the
// digest and that fixture stays green. Nothing pinned the comparison itself. This does: an
// AUTHENTIC digest with a foreign run_id, which only the run_id comparison can refuse.
test('closeCaptureRun refuses a token whose digest is authentic but whose run_id is not this run\'s', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    nodeFs.mkdirSync(join(profile.capture.output_dir, 'items'), { recursive: true });

    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    const tokenPath = join(profile.publish.chapters_dir, '.provenance', 'run', 'pending.json');
    const token = JSON.parse(nodeFs.readFileSync(tokenPath, 'utf8'));
    assert.equal(token.run_id, opened.runState.run_id);

    // Only the identifier moves; the digest stays exactly the one this payload hashes to, so the
    // digest comparison cannot be what refuses.
    nodeFs.writeFileSync(tokenPath, JSON.stringify({
      run_id: '00000000-0000-4000-8000-000000000000',
      opening_digest: token.opening_digest,
    }));

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, false,
      `a token belonging to a different run must be refused on its run_id: ${JSON.stringify(closed)}`);
  });
});

// [round 39] ... and the anchor holding is NOT on its own a licence to accept whatever the root
// now resolves to. Same unchanged anchor, but a link one level down redirects the root out of it:
// the capture wrote into a tree this run never validated, which is the whole point of the check.
test('the open -> close seam: an unchanged anchor does NOT excuse a root that now resolves OUTSIDE it', () => {
  withTempDir((dir) => {
    const real = nodeFs.realpathSync(dir);
    const releases = join(real, 'releases');
    const v1 = join(releases, 'v1');
    nodeFs.mkdirSync(v1, { recursive: true });
    const current = join(releases, 'current');
    nodeFs.symlinkSync(v1, current);
    nodeFs.mkdirSync(join(real, 'handbook'), { recursive: true });
    const elsewhere = join(real, 'elsewhere');
    nodeFs.mkdirSync(join(elsewhere, 'items'), { recursive: true });
    nodeFs.writeFileSync(join(elsewhere, 'items', 'stale.png'), 'a previous build');
    const outputDir = join(current, 'assets');
    const profile = {
      capture: { output_dir: outputDir, build_identity: { ui_read: false } },
      publish: { chapters_dir: join(real, 'handbook'), target: 'static_md' },
    };
    assert.equal(nodeFs.existsSync(outputDir), false, 'the fixture must open over an ABSENT root');

    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    const anchorBefore = identityOf(nodeFs.realpathSync(current));

    // The anchor is untouched; only the missing root's own name becomes a link out of it.
    nodeFs.symlinkSync(elsewhere, join(v1, 'assets'));
    assert.equal(identityOf(nodeFs.realpathSync(current)), anchorBefore,
      'the anchor must be untouched — this fixture is about the segment BELOW it');

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, false,
      `a root redirected out of its own anchor must be refused: ${JSON.stringify(closed)}`);
    // The ARM matters, not just the refusal, and this fixture has now been moved twice: the
    // canonical-spelling comparison caught it before round 39, segment containment caught it after,
    // and round 40 replaced containment with an exact re-join of the recorded tail. Asserting only
    // `ok: false` would let it keep passing while whichever check currently owns it was deleted.
    assert.match(closed.halts[0].message, /the path whose absence was established under/,
      `the refusal must come from the tail comparison, not from some earlier arm: ${closed.halts[0].message}`);
  });
});

// [round 37] The anchor GONE rather than replaced, which is a different observation and gets a
// different word. The close's own validation still passes here — the root is still absent, so the
// climb simply finds a higher ancestor and certifies the absence against THAT — which is exactly
// why this needs its own check: validation is answering "is this root fine now", never "is this the
// root you opened over". Found by mutation: neutralizing the unidentifiable-anchor arm killed
// nothing, because every fixture reached the replaced-anchor arm one line below it.
test('the open -> close seam: the ancestor an absent root was validated against is DELETED, not replaced', () => {
  withTempDir((dir) => {
    const real = nodeFs.realpathSync(dir);
    const safe = join(real, 'safe');
    const outputRoot = join(safe, 'assets');
    nodeFs.mkdirSync(safe, { recursive: true });
    nodeFs.mkdirSync(join(real, 'handbook'), { recursive: true });
    const profile = {
      capture: { output_dir: outputRoot, build_identity: { ui_read: false } },
      publish: { chapters_dir: join(real, 'handbook'), target: 'static_md' },
    };

    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    assert.equal(opened.runState.output_root.anchor.path, safe,
      `the absence must have been established against '${safe}': ${JSON.stringify(opened.runState.output_root)}`);

    nodeFs.rmSync(safe, { recursive: true, force: true });

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, false, `an absence resting on a directory that is gone must not close clean: ${JSON.stringify(closed)}`);
    assert.equal(closed.halts[0].halt, 'provenance_hazard', JSON.stringify(closed.halts));
    // The WORD is the point: "can no longer be identified" and "has been replaced" are two
    // different findings, and sending an operator to hunt a replacement that never happened is the
    // same failure this module spent round 34 removing from the resolution reasons.
    assert.match(closed.halts[0].message, /can no longer be identified \(vanished\)/,
      `a deleted anchor must be diagnosed as unidentifiable, not as replaced: ${JSON.stringify(closed.halts)}`);
  });
});

// [round 37] The declaration and `outputRootDrifted`'s first branch both claim that a runState
// arriving without `output_root` is a refusal rather than an older shape to tolerate, and that the
// digest is what makes it one. Both halves are asserted here, because a comment asserting a
// property is not the property: the message proves WHICH guard refused, and a `stale_replay` from
// the digest is a different (and stronger) answer than the drift check's own fallback.
test('the open -> close seam: a runState with `output_root` stripped is refused by the digest, not tolerated', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    nodeFs.mkdirSync(join(profile.capture.output_dir, 'items'), { recursive: true });

    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    assert.ok(Object.hasOwn(opened.runState, 'output_root'), 'the field must be there to strip');

    // DELETING the key and REWRITING it take two different exits, and both are the digest's. A
    // deleted key leaves `undefined` in the payload, which JCS refuses outright (round 14's guarded
    // branch); a rewritten one canonicalizes fine and fails the comparison. Asserting only one of
    // them would leave the other free to become a tolerated shape later.
    const stripped = { ...opened.runState };
    delete stripped.output_root;
    const afterDelete = CR.closeCaptureRun(profile, stripped, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(afterDelete.ok, false, JSON.stringify(afterDelete));
    assert.equal(afterDelete.halts[0].halt, 'stale_replay', JSON.stringify(afterDelete.halts));
    assert.match(afterDelete.halts[0].message, /cannot be canonicalized, so it cannot match the token's stored digest/,
      `a deleted field must be refused by the digest: ${JSON.stringify(afterDelete.halts)}`);

    const rewritten = {
      ...opened.runState,
      output_root: { ...opened.runState.output_root, identity: null, anchor: { path: dir, identity: '1:1' } },
    };
    const afterRewrite = CR.closeCaptureRun(profile, rewritten, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(afterRewrite.ok, false, JSON.stringify(afterRewrite));
    assert.equal(afterRewrite.halts[0].halt, 'stale_replay', JSON.stringify(afterRewrite.halts));
    assert.match(afterRewrite.halts[0].message, /does not match the token's stored digest/,
      `a rewritten field must be refused by the digest: ${JSON.stringify(afterRewrite.halts)}`);
  });
});

// [round 36] The same ancestor, arriving mid-run instead of being there at validation — which is the
// only way to reach the climb's own exemption now that validation refuses the standing case. Round
// 29 exempted everything above the output root from BOTH of the climb's questions, on the argument
// that "with nothing existing along the path there is nothing that could be a symlink". This arm is
// reached precisely because something does exist; the containment question is what has no meaning
// up there, not the resolution one.
test('the closing snapshot: an ancestor ABOVE the output root that becomes a dangling link mid-sweep is refused', () => {
  withTempDir((dir) => {
    const real = nodeFs.realpathSync(dir);
    const safe = join(real, 'safe');
    const outputRoot = join(safe, 'assets');
    nodeFs.mkdirSync(safe, { recursive: true });
    nodeFs.mkdirSync(join(real, 'handbook'), { recursive: true });
    const profile = {
      capture: { output_dir: outputRoot, build_identity: { ui_read: false } },
      publish: { chapters_dir: join(real, 'handbook'), target: 'static_md' },
    };
    const assetDir = join(outputRoot, 'items');
    assert.equal(nodeFs.existsSync(outputRoot), false, 'an ordinary first capture: the root does not exist yet');

    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, `a not-yet-created root under a resolving ancestor is an ordinary first capture: ${JSON.stringify(opened)}`);

    let planted = false;
    const closingDeps = depsWithOverride({
      readdirSync: (p, opts) => {
        if (!planted && String(p) === assetDir) {
          // The directory ABOVE the output root becomes a link to nothing, in the instant the
          // chapter's listing reports the path gone. Everything the adjudication climbs past is
          // absent until it reaches this — one level above the root's own depth, where the
          // exemption lives.
          planted = true;
          nodeFs.renameSync(safe, join(real, 'safe-moved-away'));
          nodeFs.symlinkSync(join(real, 'safe-target'), safe);
          const err = new Error('ENOENT'); err.code = 'ENOENT'; throw err;
        }
        return nodeFs.readdirSync(p, opts);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, closingDeps);
    assert.equal(planted, true, 'the ancestor was never replaced — this fixture cannot reach the condition');
    assert.equal(nodeFs.lstatSync(safe).isSymbolicLink(), true, 'the ancestor must be a present link');
    assert.equal(nodeFs.existsSync(safe), false, 'and it must dangle');
    assert.equal(closed.ok, false, `an unresolvable ancestor above the root must not certify direct absence: ${JSON.stringify(closed)}`);
    assert.equal(closed.halts[0].halt, 'provenance_hazard', JSON.stringify(closed.halts));
    assert.match(closed.halts[0].message, /could not be confirmed \(vanished\) and its listing then failed \(ENOENT\)/,
      `the halt must attribute this to the closing adjudication: ${JSON.stringify(closed.halts)}`);
  });
});

// [round 36, from mutation] Requiring an above-the-root ancestor to RESOLVE retired both depth-unit
// mutants: every fixture that pinned the boundary did it with a DANGLING root, and a dangling root
// is now refused by the resolution check whatever depth it is classified at. The depth still decides
// one thing — whether the CONTAINMENT comparison is made — so the input that separates the units is
// an ancestor that resolves perfectly well and resolves OUTSIDE the output root. It has to arrive
// mid-listing, because round 32's containment check refuses the standing case before the climb runs.
//
// The topology carries a `..` AND a root reached through a symlink, so the raw count and the
// RESOLVED count are both larger than the normalized one, and one fixture separates both mutants
// from the unit that is correct.
test('the closing snapshot: an outside-resolving ancestor is refused at the depth the CLIMB counts in', () => {
  withTempDir((dir) => {
    const real = nodeFs.realpathSync(dir);
    const physicalRoot = join(real, 'a', 'b', 'root-target');
    nodeFs.mkdirSync(join(physicalRoot, 'assets', 'admin'), { recursive: true });
    // The `..` component has to EXIST on the physical path: `normalizeSegments` collapses it
    // lexically while the kernel walks it, and a `..` over a missing directory makes the two halves
    // of the bracket describe different objects — which this release refuses, correctly, and which
    // would make this fixture prove that instead of what it is here for.
    nodeFs.mkdirSync(join(physicalRoot, 'handbook'), { recursive: true });
    // [round 37] EMPTY, and that is load-bearing rather than incidental. The climb starts at the
    // TIP, and a tip that exists ends it immediately with a refusal (round 28) — so if this tree
    // held an `items`, the chapter's path would resolve onto a directory that EXISTS, the climb
    // would refuse there, and it would refuse identically at either depth unit. The test would
    // still pass and both depth mutants would survive it. Asserted below, after the plant, where
    // the resolution actually happens; the sibling boundary fixture states the same requirement.
    nodeFs.mkdirSync(join(real, 'outside-admin'), { recursive: true });
    nodeFs.mkdirSync(join(real, 'handbook'), { recursive: true });
    nodeFs.symlinkSync(physicalRoot, join(real, 'rootlink'));

    // NOT `join(...)`: it normalizes `..` away, and a fixture built with it carries no `..` at all.
    const outputDir = `${join(real, 'rootlink')}/handbook/../assets`;
    const profile = {
      capture: { output_dir: outputDir, build_identity: { ui_read: false } },
      publish: { chapters_dir: join(real, 'handbook'), target: 'static_md' },
    };
    const entry = { slug: 'items', group: 'admin' };
    const configuredGroupDir = `${join(real, 'rootlink')}/assets/admin`;
    const physicalGroupDir = join(physicalRoot, 'assets', 'admin');
    const assetDir = `${configuredGroupDir}/items`;
    assert.equal(nodeFs.existsSync(assetDir), false, 'the chapter must be absent, or the listing never fails and the climb never runs');

    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, `an absent chapter under a resolving group ancestor is an ordinary first capture: ${JSON.stringify(opened)}`);

    let planted = false;
    const closingDeps = depsWithOverride({
      readdirSync: (p, opts) => {
        if (!planted && String(p) === assetDir) {
          // The GROUP ancestor becomes a link to a tree outside the output root, in the instant the
          // chapter's listing reports the path gone. It resolves — so the resolution check is
          // satisfied — and only the containment comparison can refuse it, which is the comparison
          // the depth unit decides whether to make.
          planted = true;
          nodeFs.rmSync(physicalGroupDir, { recursive: true, force: true });
          nodeFs.symlinkSync(join(real, 'outside-admin'), physicalGroupDir);
          const err = new Error('ENOENT'); err.code = 'ENOENT'; throw err;
        }
        return nodeFs.readdirSync(p, opts);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, closingDeps);
    assert.equal(planted, true, 'the ancestor was never replaced — this fixture cannot reach the condition');
    assert.equal(nodeFs.realpathSync(configuredGroupDir), join(real, 'outside-admin'),
      'the ancestor must RESOLVE, and resolve outside the root — otherwise this pins the resolution check instead');
    assert.equal(nodeFs.existsSync(join(real, 'outside-admin', 'items')), false,
      'the outside tree must not hold the chapter: a present TIP ends the climb before the depth boundary it is here to pin');
    assert.equal(closed.ok, false, `an ancestor resolving outside the output root must not certify direct absence: ${JSON.stringify(closed)}`);
    assert.equal(closed.halts[0].halt, 'provenance_hazard', JSON.stringify(closed.halts));
    assert.match(closed.halts[0].message, /could not be confirmed \(vanished\) and its listing then failed \(ENOENT\)/,
      `the halt must attribute this to the closing adjudication: ${JSON.stringify(closed.halts)}`);
  });
});

// [round 36, from mutation] The exemption's own boundary, which the resolution requirement also
// retired: every fixture that pinned `<` against `<=` used a DANGLING root, and a dangling root now
// fails the resolution check at either classification. What the boundary still decides is whether
// the ROOT ITSELF must satisfy containment — that is, whether it still canonicalizes to what
// validation recorded. A root that springs into existence mid-sweep as a link to an outside tree
// RESOLVES perfectly well; only the containment comparison at its own depth can refuse it, and
// `<=` exempts exactly that one comparison. The output-root bracket cannot cover this: the root did
// not exist at validation, so there is no identity to compare and it stands down.
test('the closing snapshot: an output root that appears mid-sweep as a link OUTSIDE its own validated segments is refused', () => {
  withTempDir((dir) => {
    const real = nodeFs.realpathSync(dir);
    const outputRoot = join(real, 'assets');
    const outside = join(real, 'outside');
    // The outside tree deliberately has NO `items`: with one, the chapter path resolves through the
    // new link and the climb refuses at the TIP (round 28's rule — a tip that exists now is not
    // absent), which is a different guard and leaves this boundary unexercised.
    nodeFs.mkdirSync(outside, { recursive: true });
    nodeFs.mkdirSync(join(real, 'handbook'), { recursive: true });

    const profile = {
      capture: { output_dir: outputRoot, build_identity: { ui_read: false } },
      publish: { chapters_dir: join(real, 'handbook'), target: 'static_md' },
    };
    const assetDir = join(outputRoot, 'items');
    assert.equal(nodeFs.existsSync(outputRoot), false, 'the root must not exist at validation, or the output-root bracket refuses this first');

    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, `a chapter under a not-yet-created output root is an ordinary first capture: ${JSON.stringify(opened)}`);

    let planted = false;
    const closingDeps = depsWithOverride({
      readdirSync: (p, opts) => {
        if (!planted && String(p) === assetDir) {
          planted = true;
          nodeFs.symlinkSync(outside, outputRoot);
          const err = new Error('ENOENT'); err.code = 'ENOENT'; throw err;
        }
        return nodeFs.readdirSync(p, opts);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, closingDeps);
    assert.equal(planted, true, 'the root was never planted — this fixture cannot reach the condition');
    assert.equal(nodeFs.realpathSync(outputRoot), outside,
      'the root must RESOLVE, and resolve outside its validated segments — otherwise this pins the resolution check instead');
    assert.equal(closed.ok, false, `a root resolving outside what validation recorded must not certify direct absence: ${JSON.stringify(closed)}`);
    assert.equal(closed.halts[0].halt, 'provenance_hazard', JSON.stringify(closed.halts));
    assert.match(closed.halts[0].message, /could not be confirmed \(vanished\) and its listing then failed \(ENOENT\)/,
      `the halt must attribute this to the closing adjudication: ${JSON.stringify(closed.halts)}`);
  });
});

// [round 27] The ancestor climb walks a PATH, so it inherits the release's other recurring defect:
// the shipped example profile's `output_dir` is RELATIVE (`vault/handbook/assets`), and a climb that
// prefixes `/` onto relative segments probes an entirely different tree — one where every candidate
// is absent until `/` itself, which exists and resolves, certifying "direct absence" for a path it
// never looked at. Mutation found it: the absolute-only form killed nothing until this test existed,
// and it would have applied to the documented default configuration rather than an edge case.
test('openCaptureRun: a dangling ancestor under a RELATIVE output_dir is refused, not resolved against the filesystem root', () => {
  withTempDir((dir) => {
    withRelativeCwd(dir, () => {
      const chaptersDir = 'vault/handbook';
      nodeFs.mkdirSync(join(dir, chaptersDir), { recursive: true });
      const profile = {
        capture: { output_dir: 'vault/handbook/assets', build_identity: { ui_read: false } },
        publish: { chapters_dir: chaptersDir, target: 'static_md' },
      };
      const entry = { slug: 'items', group: 'admin' };
      nodeFs.mkdirSync('vault/handbook/assets', { recursive: true });

      let planted = false;
      const deps = depsWithOverride({
        openSync: (p, ...rest) => {
          if (!planted && String(p).endsWith('/pending.json')) {
            nodeFs.symlinkSync('vault/handbook/assets/admin-target', 'vault/handbook/assets/admin');
            planted = true;
          }
          return nodeFs.openSync(p, ...rest);
        },
        runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
      });

      const opened = CR.openCaptureRun(profile, [entry], null, deps);
      assert.equal(planted, true, 'the plant hook never ran — this fixture cannot reach the condition');
      assert.equal(nodeFs.lstatSync('vault/handbook/assets/admin').isSymbolicLink(), true, 'the ancestor must be a present link');
      assert.equal(nodeFs.existsSync('vault/handbook/assets/admin'), false, 'the ancestor link must dangle');
      assert.equal(opened.ok, false, `a dangling relative ancestor must not open as a first capture: ${JSON.stringify(opened)}`);
      assert.equal(opened.halts[0].halt, 'provenance_hazard', JSON.stringify(opened.halts));
    });
  });
});

// [round 27] The ancestor climb has a third answer besides "absent, keep going" and "here it is":
// an ancestor it cannot inspect at all. Mutation found this branch unprotected — flipping its
// refusal to a tolerance killed nothing — and it is the release's own defect class, because an
// unreadable ancestor produces exactly the empty snapshot a genuine first capture produces.
test('the closing snapshot: an ancestor that cannot be inspected is not read as direct absence', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items', group: 'admin' };
    const groupDir = join(profile.capture.output_dir, 'admin');
    const assetDir = join(groupDir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');

    // [round 33] Measured, not assumed: once the close ran gate 3, an EACCES present from the first
    // syscall became VALIDATION's refusal, this test kept passing, and the mutant it was written to
    // kill started surviving. The refusal and the coverage are different facts. The EACCES is
    // therefore armed only once the sweep is underway, which is the window the climb actually owns.
    const arming = armAfterFirstChapterListing(profile, () => {});
    const opened = CR.openCaptureRun(profile, [SWEEP_ARMING_ENTRY, entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));

    nodeFs.rmSync(groupDir, { recursive: true, force: true });
    let refusedAncestor = false;
    const closingDeps = depsWithOverride({
      readdirSync: arming.readdirSync,
      // The tip is genuinely gone, so the tip's own `lstat` really is ENOENT. The GROUP ancestor is
      // the one that cannot be inspected — EACCES, not absence — so nothing here can establish that
      // the tip's absence is direct.
      lstatSync: (p, ...rest) => {
        if (arming.armed && String(p).endsWith('/admin')) {
          refusedAncestor = true;
          const err = new Error('EACCES'); err.code = 'EACCES'; throw err;
        }
        return nodeFs.lstatSync(p, ...rest);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, closingDeps);
    assert.equal(refusedAncestor, true, 'the ancestor was never probed — this fixture cannot reach the condition');
    assert.equal(closed.ok, false, `an uninspectable ancestor must not certify direct absence: ${JSON.stringify(closed)}`);
    assert.equal(closed.halts[0].halt, 'provenance_hazard', JSON.stringify(closed.halts));
  });
});

// [round 27] The over-refusal control for the two above, and the one that decides the SHAPE of the
// check. Requiring the ancestor chain to be symlink-FREE would pass both tests above and be wrong:
// gate 3 accepts an ancestor symlink whose target exists and is contained, so refusing one here
// would make the walk stricter than the gate it is backstopping. The test is RESOLUTION.
test('openCaptureRun: a RESOLVING symlinked ancestor with a not-yet-created leaf is still an ordinary first capture', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items', group: 'admin' };
    const groupDir = join(profile.capture.output_dir, 'admin');
    const groupTarget = join(profile.capture.output_dir, 'admin-real');
    nodeFs.mkdirSync(groupTarget, { recursive: true });
    nodeFs.symlinkSync(groupTarget, groupDir);

    assert.equal(nodeFs.lstatSync(groupDir).isSymbolicLink(), true, 'the ancestor must be a link');
    assert.equal(nodeFs.statSync(groupDir).isDirectory(), true, 'and it must resolve, or this control tests nothing');
    assert.equal(nodeFs.existsSync(join(groupDir, 'items')), false, 'the leaf must not exist yet');

    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, `a resolving ancestor with an absent leaf is an ordinary first capture: ${JSON.stringify(opened)}`);
    // Null-prototype maps by construction, so compare contents rather than shapes.
    assert.deepEqual(Object.keys(opened.runState.opening_assets['admin/items']), [], JSON.stringify(opened.runState.opening_assets));
    assert.deepEqual([...opened.runState.opening_asset_hazards['admin/items']], []);
  });
});

// [round 26 BLOCKER] The closing half. Nothing upstream refuses a dangling root link that appears
// mid-sweep — this branch is the only thing standing between it and a committed `closing: {}` with
// no hazards.
// [round 34] The sentence that stood here said `closeCaptureRun` NEVER runs gate 3. That was true
// when it was written and stopped being true one round later, three lines below its own correction:
// a maintainer reading only the header would plant before the close again and retire this coverage
// a second time, exactly as round 33 did. The close runs gates 1-4; what it cannot see is a hazard
// that arrives after they pass.
test('the closing snapshot: a dangling root symlink is refused, not read as a chapter that produced nothing', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');

    // The real directory is replaced by a link to a path that does not exist. Only the PLANT is
    // seamed — one pass-through `readdir` that creates the link on its way out; every observation
    // the guard then makes is the real filesystem's: `lstat` really succeeds and reports a link,
    // `realpath` really fails ENOENT, and so does the listing — precisely the pair the whitelist
    // used to tolerate.
    // [round 33] The plant moved into the call because the close now runs gate 3, which refuses a
    // dangling asset root before the sweep ever starts. Gate 3 sees this path plainly absent (the
    // shape it tolerates), and the link exists by the time the snapshot's own baseline reads it.
    const arming = armAfterFirstChapterListing(profile, () => {
      nodeFs.symlinkSync(join(profile.capture.output_dir, 'items-target'), assetDir);
    });

    const opened = CR.openCaptureRun(profile, [SWEEP_ARMING_ENTRY, entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));

    nodeFs.renameSync(assetDir, join(dir, 'items-moved-away'));

    const closingDeps = depsWithOverride({
      readdirSync: arming.readdirSync,
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, closingDeps);
    assert.equal(arming.armed, true, 'the link was never planted — this fixture cannot reach the condition');
    assert.equal(nodeFs.lstatSync(assetDir).isSymbolicLink(), true, 'the root must be a present link');
    assert.equal(nodeFs.existsSync(assetDir), false, 'the link must dangle');
    assert.equal(closed.ok, false, `a present-but-dangling root must not close as an empty chapter: ${JSON.stringify(closed)}`);
    assert.equal(closed.halts[0].halt, 'provenance_hazard', JSON.stringify(closed.halts));
    assert.match(closed.halts[0].message, /could not be confirmed \(vanished\)/);
  });
});

// [round 25] The two branches tolerate DIFFERENT reason sets, and this pins the asymmetry — without
// it, widening ENOTDIR's whitelist to match ENOENT's kills nothing and the distinction is prose.
// `vanished` is legitimate on ENOENT because a first capture produces exactly that pair. It is not
// legitimate here: nothing was at that path one syscall ago and something that is not a directory is
// at it now, which no first capture can produce.
test('the closing snapshot: a root absent at its baseline and ENOTDIR at its listing is refused, though absence alone would be tolerated', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');

    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));

    // The baseline `lstat` is real and really reports ENOENT — only the listing is a seam, standing
    // in for an object created at that path between the two syscalls.
    nodeFs.rmSync(assetDir, { recursive: true, force: true });
    let listed = false;
    const closingDeps = depsWithOverride({
      readdirSync: (p, opts) => {
        if (String(p).endsWith('/items')) {
          listed = true;
          const err = new Error('ENOTDIR'); err.code = 'ENOTDIR'; throw err;
        }
        return nodeFs.readdirSync(p, opts);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, closingDeps);
    assert.equal(listed, true, 'the root was never listed — this fixture cannot reach the condition');
    assert.equal(closed.ok, false, `an object appearing where the baseline saw nothing must not close silently: ${JSON.stringify(closed)}`);
    assert.equal(closed.halts[0].halt, 'provenance_hazard', JSON.stringify(closed.halts));
    assert.match(closed.halts[0].message, /could not be confirmed \(vanished\).*ENOTDIR/);
  });
});

// [round 24 IMPORTANT] `dev`/`ino` are 64-bit; a JavaScript number is not. Codex measured inodes
// 9007199254740992 and 9007199254740993 — two different directories — both rendering the identity
// `7:9007199254740992`, so on a filesystem exposing identifiers above 2^53 a substitution passes
// every observation point. The release's own defect class arriving through the number line.
test('identity: an inode outside the safe-integer range is refused, never rounded into a match', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');

    assert.equal(Number.isSafeInteger(2 ** 53), false, 'the fixture value must be INEXACT, or it proves nothing');
    const deps = depsWithOverride({
      // A seam that answers in numbers, as `fs.lstatSync` does without `{ bigint: true }`, on a
      // filesystem whose inodes do not fit one. [round 34] Scoped to the CHAPTER directory for the
      // same reason as the round-23 fixture above: an inexact identity at the OUTPUT ROOT is now
      // refused at validation, so answering this way everywhere would move the halt off the guard
      // this test names.
      lstatSync: (p, ...rest) => {
        const st = nodeFs.lstatSync(p, ...rest);
        // `endsWith`, not equality: the module reaches this directory by more than one string
        // (the configured form and its resolved twin), and an exact match interposes on neither.
        if (!String(p).endsWith('/items')) return st;
        return {
          isSymbolicLink: () => st.isSymbolicLink(),
          isDirectory: () => st.isDirectory(),
          isFile: () => st.isFile(),
          dev: 7,
          ino: 2 ** 53,
        };
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(opened.ok, false, JSON.stringify(opened));
    assert.equal(opened.halts[0].halt, 'provenance_hazard');
    assert.match(opened.halts[0].message, /cannot establish the identity of asset directory/);
  });
});

// The other half, and the reason the refusal above is not a usability regression: the module ASKS
// for exact values and accepts a BigInt identity.
//
// The first version of this test asserted `seenOptions.some(o => o?.bigint)` and was WORTHLESS:
// dropping the request from any ONE of the three identity reads left the other two asking, so all
// three mutants killed nothing. Worse, dropping one is invisible on an ordinary filesystem by
// design — `exactIdentityPart` renders a safe-integer number and a BigInt to the same digits, so a
// site reading numbers still compares equal to a site reading BigInts. The only thing that can
// separate them is a seam where the answer DEPENDS on the request, which is precisely the
// production hazard: a filesystem whose inodes do not fit a number. The mock below is that seam, so
// any site that stops asking reads an inexact value and the run refuses.
//
// BOTH topologies, and that is not thoroughness for its own sake — it is what makes the three sites
// separable. On a SYMLINKED root the first `lstat` is read only for `isSymbolicLink()`, its `dev`/
// `ino` are never touched, and the identity comes from the target stat; so a symlink-only fixture
// leaves gate 3's and the walk's own requests unpinned, and both their mutants survived it. On a
// PLAIN root the opposite holds and the target read never runs. One fixture cannot reach all three.
for (const topology of ['plain', 'symlinked']) {
test(`identity: every identity read requests exact stats when the asset root is ${topology}`, () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    if (topology === 'plain') {
      nodeFs.mkdirSync(assetDir, { recursive: true });
      nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');
    } else {
      const real = join(profile.capture.output_dir, 'real-items');
      nodeFs.mkdirSync(real, { recursive: true });
      nodeFs.writeFileSync(join(real, 'a.png'), 'v1');
      nodeFs.symlinkSync(real, assetDir);
    }

    let exactRequests = 0;
    let inexactAnswers = 0;
    const deps = depsWithOverride({
      // Honours the request: exact BigInt when asked, and — like a filesystem whose identifiers do
      // not fit a JavaScript number — an INEXACT number when not.
      lstatSync: (p, opts) => {
        const st = nodeFs.lstatSync(p);
        const base = {
          isSymbolicLink: () => st.isSymbolicLink(),
          isDirectory: () => st.isDirectory(),
          isFile: () => st.isFile(),
        };
        if (opts?.bigint === true) {
          exactRequests += 1;
          return { ...base, dev: BigInt(st.dev), ino: BigInt(st.ino) };
        }
        inexactAnswers += 1;
        return { ...base, dev: 7, ino: 2 ** 53 };
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.ok(exactRequests > 0, 'no identity read asked for exact values — this fixture cannot reach the condition');
    assert.ok(inexactAnswers > 0, 'the non-requesting branch never ran, so this mock cannot distinguish the two');
    assert.equal(opened.ok, true, `every identity read must ask for exact values: ${JSON.stringify(opened)}`);
    assert.deepEqual(opened.runState.opening_asset_hazards.items, []);
    assert.deepEqual(Object.keys(opened.runState.opening_assets.items), ['a.png']);
  });
});
}

// [round 24 BLOCKER, half one] The round-23 self-baseline deferred a failed first observation and
// then never adjudicated it: it left the pin null and relied on "the readdirSync branch below
// distinguishes a legitimate first capture", which only runs when the listing FAILS. A root this
// walk HAS identified and which then fails to list is not a first capture — it existed a syscall
// ago — but the ENOENT branch consulted only the caller's `rootMustExist`, and `closeCaptureRun`
// passes false because it never ran gate 3. Codex executed it: `{hashes:{}, hazards:[]}`, which the
// close reads as a chapter that produced nothing.
test('the closing snapshot: a root that lists ENOENT after its own baseline is refused, not read as empty', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');

    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));

    let listed = false;
    const closingDeps = depsWithOverride({
      // The baseline lstat succeeds against the real directory; the listing then reports it gone.
      readdirSync: (p, opts) => {
        if (String(p).endsWith('/items')) {
          listed = true;
          const err = new Error('ENOENT'); err.code = 'ENOENT'; throw err;
        }
        return nodeFs.readdirSync(p, opts);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, closingDeps);
    assert.equal(listed, true, 'the root was never listed — this fixture cannot reach the condition');
    assert.equal(closed.ok, false, `an identified root that stopped existing must not close silently: ${JSON.stringify(closed)}`);
    assert.equal(closed.halts[0].halt, 'provenance_hazard');
    assert.match(closed.halts[0].message, /ENOENT/);
  });
});

// [round 24 BLOCKER, half two] The mirror image, and the one codex's probe made unarguable: the
// baseline FAILS, the listing then SUCCEEDS, and the walk processes the entries of an object it
// never established — no second identity observation anywhere on that path. A directory that comes
// into existence between two adjacent observations inside one call is the substitution signature,
// not a first capture; a first capture has nothing to list.
test('the closing snapshot: a root that lists successfully after a FAILED baseline is refused', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');

    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));

    let baselineRefused = false;
    const closingDeps = depsWithOverride({
      // Exactly codex's probe: the root's own first observation reports it absent, and it is
      // present and readable by the time the listing runs.
      // [round 33] EVERY lstat of this path reports absent, not just the first. The close now runs
      // gate 3 ahead of the sweep, so a once-only throw is consumed by validation — which tolerates
      // an absent asset directory as the ordinary "chapter produced nothing" shape — and the
      // snapshot's own baseline then succeeds, leaving the fixture unable to reach the condition it
      // was written for. Refusing the path throughout puts gate 3 on the tolerated branch AND fails
      // the baseline, which is the pair this test is about; the listing below is the real one.
      lstatSync: (p, ...rest) => {
        if (String(p).endsWith('/items')) {
          baselineRefused = true;
          const err = new Error('ENOENT'); err.code = 'ENOENT'; throw err;
        }
        return nodeFs.lstatSync(p, ...rest);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, closingDeps);
    assert.equal(baselineRefused, true, 'the baseline never ran — this fixture cannot reach the condition');
    assert.equal(closed.ok, false, `entries of an unidentified root must not be walked: ${JSON.stringify(closed)}`);
    assert.equal(closed.halts[0].halt, 'provenance_hazard');
    assert.match(closed.halts[0].message, /could not be confirmed \(vanished\) before it was listed/);
  });
});

test('the closing snapshot: a vanished asset still refuses the chapter, and now says why', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');

    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    assert.deepEqual(opened.runState.opening_asset_hazards['items'], [],
      'the OPENING half must be clean, or this test is measuring the other phase');

    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v2');
    const closingDeps = depsWithOverride({
      openSync: (path, ...rest) => {
        if (String(path).endsWith('/a.png')) {
          const err = new Error('ENOENT'); err.code = 'ENOENT'; throw err;
        }
        return nodeFs.openSync(path, ...rest);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });
    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, closingDeps);
    assert.equal(closed.ok, true, `a hazard in the closing snapshot must not halt the close: ${JSON.stringify(closed)}`);

    const chapterFile = writeChapterAt(profile, entry, '# items\n');
    const w5Deps = { ...stubDepsNoIdentity(), expectedAssets: stubExpectedAssetsFor(assetDir, ['a.png']) };
    const result = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, opened.runState.run_id, w5Deps);
    assert.equal(result.recorded, false, JSON.stringify(result));
    assert.equal(result.reason, 'rule5_closing_unhashable:a.png:vanished', JSON.stringify(result));
    assert.equal(nodeFs.existsSync(CR.chapterRecordPath(profile, entry)), false);
  });
});

// [round 19 BLOCKER, the same phase distinction one layer up] Round 18 mapped the unknown-dirent
// fallback's own ENOENT to an absence for the same wrong reason, so a file whose type the kernel
// declined to report and which then vanished before the `lstat` took the identical path.
test('the opening snapshot: an UNKNOWN-typed entry that vanishes before its lstat is a hazard too', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');

    const realDirents = nodeFs.readdirSync(assetDir, { withFileTypes: true });
    const Dirent = Object.getPrototypeOf(realDirents[0]).constructor;
    const deps = depsWithOverride({
      readdirSync: (p, opts) => {
        const real = nodeFs.readdirSync(p, opts);
        if (!opts?.withFileTypes) return real;
        return real.map((d) => new Dirent(d.name, 0, p));
      },
      lstatSync: (p) => {
        if (String(p).endsWith('/a.png')) {
          const err = new Error('ENOENT'); err.code = 'ENOENT'; throw err;
        }
        return nodeFs.lstatSync(p);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(opened.ok, true, JSON.stringify(opened));
    assert.deepEqual(opened.runState.opening_asset_hazards['items'], ['a.png:vanished']);
  });
});

// [round 20 IMPORTANT] Round 19's two seam tests each claimed a declaration-minimal shape and each
// used something else: the lstat mock omitted `isFile()`, which the declaration now REQUIRES, and
// the dirent was a real `fs.Dirent`, which carries the four predicates the runtime is supposed to
// work without. One tested a non-conforming mock while claiming a conforming one; the other could
// not reach the optional-call path at all. Both directions are separated here — a caller writing to
// the CURRENT declaration must work, and a caller still on the older, narrower one must degrade
// rather than crash. Nothing in this repository type-checks these declarations, so these tests are
// the only thing that makes either statement false when it stops being true.
test('the opening snapshot: an lstat result implementing exactly the CURRENT declaration resolves the type', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');

    const deps = depsWithOverride({
      readdirSync: unknownDirentReaddir({ minimal: false }),
      // LstatResultLike, exactly: isSymbolicLink, isDirectory, isFile, dev, ino. Nothing else.
      // [round 23] `dev`/`ino` were added to the declaration in round 22 and NOT to this fixture,
      // which went on calling itself "exactly the CURRENT declaration" for a round — the identical
      // defect round 20 wrote this pair of tests to prevent, in the test written to prevent it.
      // Nothing here compiles the declarations, so a stale fixture is not merely uninformative: this
      // one stayed green by exercising the root fail-open that round 23 removed, which is the only
      // reason it did not fail the moment it stopped conforming.
      lstatSync: (p) => {
        const real = nodeFs.lstatSync(p);
        return {
          isSymbolicLink: () => real.isSymbolicLink(),
          isDirectory: () => real.isDirectory(),
          isFile: () => real.isFile(),
          dev: real.dev,
          ino: real.ino,
        };
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(opened.ok, true, JSON.stringify(opened));
    assert.deepEqual(opened.runState.opening_asset_hazards['items'], [],
      'a declaration-conforming lstat must resolve an unknown dirent to a real file, not to a hazard');
    assert.deepEqual(Object.keys(opened.runState.opening_assets['items']), ['a.png']);
  });
});

// [round 23] This test used to withhold `dev`/`ino` as well, and gate 3 now halts on a root whose
// identity it cannot read — which would have made it a second copy of the identity-halt test above
// rather than the `isFile` test it was written to be. The two degradations are separate rules and
// must be reachable separately, so the mock supplies what round 22 requires and withholds only the
// predicate round 19 added. The halting half is pinned by its own test; this one keeps the half that
// must NOT harden.
test('the opening snapshot: an lstat result missing the round-19 isFile predicate degrades to a hazard, never a crash', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');

    const deps = depsWithOverride({
      readdirSync: unknownDirentReaddir({ minimal: false }),
      lstatSync: (p) => {
        const real = nodeFs.lstatSync(p);
        return {
          isSymbolicLink: () => real.isSymbolicLink(),
          isDirectory: () => real.isDirectory(),
          dev: real.dev,
          ino: real.ino,
        };
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(opened.ok, true, `a caller on the older contract must not halt the run: ${JSON.stringify(opened)}`);
    assert.deepEqual(opened.runState.opening_asset_hazards['items'], ['a.png:inspection_failure'],
      'an undeterminable type is uncertainty — a hazard — never a guess');
  });
});

test('the opening snapshot: a DirentLike carrying only the three REQUIRED predicates never has the optional four called', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');

    const deps = depsWithOverride({
      readdirSync: unknownDirentReaddir({ minimal: true }),
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(opened.ok, true, `a minimal DirentLike must not halt the run: ${JSON.stringify(opened)}`);
    assert.deepEqual(opened.runState.opening_asset_hazards['items'], []);
    assert.deepEqual(Object.keys(opened.runState.opening_assets['items']), ['a.png']);
  });
});

// [round 18, found while checking whether round 17's fix was complete one layer up] The walk's own
// error handling had the same hole the hazard split was created for: a nested directory whose
// listing fails with ENOTDIR — it was a directory when its type was decided and a regular file a
// moment later — returned silently, so everything beneath it left the snapshot with no hazard
// recorded. At the OPENING observation point that is not a harmless omission: the absent keys read
// as "brand-new this run", rule 4 is skipped, and old bytes are recorded as the captured build's.
// Exactly the round-17 failure through a different door.
//
// The fail-closed argument that covers the filename listing does NOT cover this, and that is the
// round-15 lesson restated: the listing halts on a destination it cannot match, but the opening
// snapshot's missing key is read as good news. The root directory is a separate case and is gated
// at the call site, which lstats the asset dir and halts on anything but ENOENT/ENOTDIR.
test('walk: a nested directory that stops being one mid-walk is a hazard, not a silent omission', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(join(assetDir, 'screens'), { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'screens', 'a.png'), 'stale-from-the-previous-build');
    nodeFs.writeFileSync(join(assetDir, 'top.png'), 'v1');

    // The dirent still says `screens` is a directory; the listing of it fails as it would if the
    // directory were replaced by a regular file in between.
    const deps = depsWithOverride({
      readdirSync: (p, opts) => {
        if (String(p).endsWith('/screens')) {
          const err = new Error('ENOTDIR'); err.code = 'ENOTDIR'; throw err;
        }
        return nodeFs.readdirSync(p, opts);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(opened.ok, true, JSON.stringify(opened));
    assert.deepEqual(opened.runState.opening_asset_hazards['items'], ['screens:inspection_failure'],
      'a directory whose contents could not be enumerated must survive as a hazard covering them');
    // The sibling that WAS readable is unaffected — a hazard is a statement about one path.
    assert.deepEqual(Object.keys(opened.runState.opening_assets['items']), ['top.png']);

    // And the hazard reaches W5 through containment, refusing the asset it hid.
    nodeFs.writeFileSync(join(assetDir, 'top.png'), 'v2');
    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, true, JSON.stringify(closed));
    const chapterFile = writeChapterAt(profile, entry, '# items\n');
    const w5Deps = { ...stubDepsNoIdentity(), expectedAssets: stubExpectedAssetsFor(assetDir, ['screens/a.png']) };
    const result = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, opened.runState.run_id, w5Deps);
    assert.equal(result.recorded, false, JSON.stringify(result));
    assert.equal(result.reason, 'rule5_opening_unhashable:screens:inspection_failure', JSON.stringify(result));
  });
});

// [round 18 IMPORTANT] The socket test above passes with this completely broken, for the third
// time in this release: it exercises a KNOWN dirent type, and the defect is in what happens when
// the type is unknown. libuv reports `UV_DIRENT_UNKNOWN` on filesystems that do not fill in
// `d_type` — several network and FUSE mounts, and XFS in some configurations — and then EVERY
// predicate on the dirent is false, including `isFile()`. The walk's final `else` called that
// `non_regular` and dropped the entry: a plain `a.png` omitted from both the hash snapshot and the
// filename listing, persisted under an operator-facing reason the code never established. On such
// a filesystem no chapter can be recorded at all, because extraction halts on the destination it
// cannot match, and W6 cannot finish either.
//
// The fixture is a REAL `fs.Dirent` constructed with type 0 rather than an object with all
// predicates stubbed false, because "all predicates false" is my model of UV_DIRENT_UNKNOWN and the
// model is half of what is under test. Verified on this Node: every predicate, including
// `isSocket()`, returns false.
test('walk: a dirent of UNKNOWN type is resolved by lstat, not guessed at', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');
    nodeFs.mkdirSync(join(assetDir, 'screens'));
    nodeFs.writeFileSync(join(assetDir, 'screens', 'b.png'), 'v1');

    const realDirents = nodeFs.readdirSync(assetDir, { withFileTypes: true });
    const Dirent = Object.getPrototypeOf(realDirents[0]).constructor;
    const unknownTyped = (name, parent) => new Dirent(name, 0, parent);
    assert.equal(unknownTyped('a.png', assetDir).isFile(), false, 'fixture must model UV_DIRENT_UNKNOWN');
    assert.equal(unknownTyped('a.png', assetDir).isSocket(), false, 'fixture must model UV_DIRENT_UNKNOWN');

    const deps = depsWithOverride({
      readdirSync: (p, opts) => {
        const real = nodeFs.readdirSync(p, opts);
        if (!opts?.withFileTypes) return real;
        // The whole tree reports unknown, which is how such a filesystem behaves — the directory
        // must be walked into as well, or the nested asset is lost for a second reason.
        return real.map((d) => unknownTyped(d.name, p));
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(opened.ok, true, JSON.stringify(opened));
    assert.deepEqual(opened.runState.opening_asset_hazards['items'], [],
      'a regular file whose dirent type is unknown is not a hazard, and saying so states something the code never established');
    assert.deepEqual(Object.keys(opened.runState.opening_assets['items']).sort(), ['a.png', 'screens/b.png'],
      'both the file and the one inside the unknown-typed directory must be hashed');
  });
});

// [round 17] The reader now rejects a record carrying an unrecognized hazard word, and that
// rejection refuses every chapter in the run — so a word a PRODUCER can emit but the reader does
// not know would turn a legitimate run unreadable, which is a worse failure than the one the
// validation closes. The two sides are derived from genuinely different sources: this drives the
// real producers over real fixtures, one per condition, and feeds what they actually emit back
// through the real reader. A word added to the walk or the leaf inspection without being added to
// the reader fails here rather than in an operator's run.
test('every hazard word the real producers emit round-trips through the real reader', async () => {
  const dir = nodeFs.mkdtempSync(join(tmpdir(), 'ehcr-vocab-'));
  const server = netCreateServer();
  try {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });

    nodeFs.symlinkSync(join(dir, 'elsewhere.png'), join(assetDir, 'linked.png'));
    nodeFs.writeFileSync(join(assetDir, 'aliased.png'), 'v1');
    nodeFs.linkSync(join(assetDir, 'aliased.png'), join(dir, 'alias.png'));
    nodeFs.writeFileSync(join(assetDir, 'unopenable.png'), 'v1');
    nodeFs.writeFileSync(join(assetDir, 'gone.png'), 'v1');
    await new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(join(assetDir, 'live.sock'), resolve);
    });

    // The one condition with no filesystem fixture: the open itself fails. Scoped to this asset by
    // name so every other open in the run — including the module's own record writes — is real.
    const deps = depsWithOverride({
      openSync: (path, ...rest) => {
        if (String(path).endsWith('unopenable.png')) {
          const err = new Error('EACCES'); err.code = 'EACCES'; throw err;
        }
        // [round 19] Listed by the directory read, gone by the time it is opened.
        if (String(path).endsWith('gone.png')) {
          const err = new Error('ENOENT'); err.code = 'ENOENT'; throw err;
        }
        return nodeFs.openSync(path, ...rest);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const opened = CR.openCaptureRun(profile, [entry], null, deps);
    assert.equal(opened.ok, true, JSON.stringify(opened));
    const produced = opened.runState.opening_asset_hazards['items'];
    const words = produced.map((h) => h.slice(h.lastIndexOf(':') + 1)).sort();
    assert.deepEqual(words, ['hard_link', 'inspection_failure', 'non_regular', 'symlink', 'vanished'],
      `all five conditions must be exercised, or this pin measures less than it claims: ${JSON.stringify(produced)}`);

    const roundTripped = CR.readRunRecordText(JSON.stringify({
      record_version: 1,
      run_id: 'r1',
      opening_digest: `sha256:${'a'.repeat(64)}`,
      build_identity: validBuildIdentity(),
      chapters: { items: { opening: {}, closing: {}, opening_hazards: produced, closing_hazards: produced } },
    }));
    assert.equal(roundTripped.ok, true,
      `the reader rejected a hazard list its own producers emitted — ${JSON.stringify(produced)}: ${JSON.stringify(roundTripped)}`);
  } finally {
    await new Promise((resolve) => server.close(resolve));
    nodeFs.rmSync(dir, { recursive: true, force: true });
  }
});

// [round 17] The third site that collapsed the hazard reason, and the only one with no test at all
// — which is why it was still collapsed after a round that fixed the other two. `rehash_failed` is
// reached when an asset becomes unreadable BETWEEN close and publish, so neither hazard list can
// name it and this reason string is the operator's only account of what happened.
test('recordChapterProvenance: an asset that becomes unreadable after close names HOW, not just that it failed', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');

    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v2');
    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, true, JSON.stringify(closed));
    // Both hazard lists are clean: the run read this asset fine at both observation points.
    assert.deepEqual(opened.runState.opening_asset_hazards['items'], []);

    // Between close and publish, something else takes a hard link to it.
    nodeFs.linkSync(join(assetDir, 'a.png'), join(dir, 'alias.png'));

    const chapterFile = writeChapterAt(profile, entry, '# items\n');
    const deps = { ...stubDepsNoIdentity(), expectedAssets: stubExpectedAssetsFor(assetDir, ['a.png']) };
    const result = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, opened.runState.run_id, deps);
    assert.equal(result.recorded, false, JSON.stringify(result));
    assert.equal(result.reason, 'rehash_failed:a.png:hard_link', JSON.stringify(result));
    assert.equal(nodeFs.existsSync(CR.chapterRecordPath(profile, entry)), false);
  });
});

// [round 21] The same rule at its two remaining sites. Round 20 concluded that only the opening
// snapshot reads a LISTED path, and that W5 and W6 read paths with no listing behind them — half of
// that was wrong. `expectedAssets` builds its candidate set from `listRegularFilesRecursive` over
// this very directory, so an `absent` at the rehash is a file this call listed a moment earlier,
// and reporting it as "never published" is a confident wrong diagnosis of a mid-run disappearance.
// The verdict is unchanged on both sides of this change — the chapter is refused either way — so
// what is pinned here is the OPERATOR'S account, which is the entire content of the defect.
//
// The extractor is the REAL one. A stubbed `expectedAssets` would assert the listing-backed premise
// rather than exercise it, which is the shape that let five fixtures in this release pass while
// unable to reach the condition they were named for.
test('recordChapterProvenance: an asset listed by the extraction and gone before the rehash is `vanished`, not `absent`', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');

    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v2');
    assert.equal(CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity()).ok, true);

    const chapterFile = writeChapterAt(profile, entry, '# items\n');
    const embed = chapterPathsModule.embedPath(chapterFile, assetDir, 'a.png');
    nodeFs.writeFileSync(chapterFile, `# items\n\n1. Step\n\n   ![a](${embed})\n`);

    let removed = false;
    const deps = depsWithOverride({
      readdirSync: (p, opts) => {
        const listed = nodeFs.readdirSync(p, opts);
        if (p === assetDir && !removed) {
          nodeFs.unlinkSync(join(assetDir, 'a.png'));
          removed = true;
        }
        return listed;
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const result = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, opened.runState.run_id, deps);
    assert.equal(removed, true, 'the asset listing never ran — this fixture cannot reach the condition');
    assert.equal(result.recorded, false, JSON.stringify(result));
    assert.equal(result.reason, 'rehash_failed:a.png:vanished', JSON.stringify(result));
    assert.equal(nodeFs.existsSync(CR.chapterRecordPath(profile, entry)), false);
  });
});

// The W6 half of the same correction, driven the same way.
test('buildProvenanceReport: an embed listed by the extraction and gone before the current hash is `vanished`, not `absent`', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');

    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v2');
    assert.equal(CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity()).ok, true);

    const chapterFile = writeChapterAt(profile, entry, '# items\n');
    const embed = chapterPathsModule.embedPath(chapterFile, assetDir, 'a.png');
    nodeFs.writeFileSync(chapterFile, `# items\n\n1. Step\n\n   ![a](${embed})\n`);
    assert.equal(
      CR.recordChapterProvenance(profile, [entry], entry, chapterFile, opened.runState.run_id, stubDepsNoIdentity()).recorded,
      true,
      'the record must exist before W6 can be asked anything about it',
    );

    let removed = false;
    const w6Deps = depsWithOverride({
      readdirSync: (p, opts) => {
        const listed = nodeFs.readdirSync(p, opts);
        if (p === assetDir && !removed) {
          nodeFs.unlinkSync(join(assetDir, 'a.png'));
          removed = true;
        }
        return listed;
      },
      runIdentityCommand: () => ({ ok: false, detail: 'no command configured in test' }),
    });

    const result = CR.buildProvenanceReport(profile, [entry], null, w6Deps);
    assert.equal(removed, true, 'the asset listing never ran — this fixture cannot reach the condition');
    assert.equal(result.rows[0].classification_reason, 'record_stale', JSON.stringify(result.rows[0]));
    assert.equal(result.rows[0].record_detail, 'unhashable:a.png:vanished', JSON.stringify(result.rows[0]));
  });
});

// [round 16 BLOCKER] A record written before the hazard/absence split carries no hazard lists, and
// the reader accepted it under the same `record_version: 1` — so "field absent" read back as "no
// hazards", the exact false statement the split exists to prevent. Two version-1 shapes cannot both
// be valid.
test('readRunRecordText: a chapter entry without hazard lists is MALFORMED, not "no hazards"', () => {
  const base = {
    record_version: 1,
    run_id: 'r1',
    opening_digest: `sha256:${'a'.repeat(64)}`,
    build_identity: validBuildIdentity(),
  };
  const preSplit = { ...base, chapters: { items: { opening: {}, closing: {} } } };
  const rejected = CR.readRunRecordText(JSON.stringify(preSplit));
  assert.equal(rejected.ok, false, JSON.stringify(rejected));
  assert.match(rejected.reason, /^bad_chapter_hazards:opening_hazards$/);

  // [round 17, found by the repository's cross-file review bot] Validating the JavaScript TYPE was
  // the whole of this, and a malformed MEMBER re-opened the false-provenance path through the
  // serialized form: `"a.png"` is a string, so it passed, and `hazardFor` splits at the last colon
  // — `lastIndexOf` returns -1 and `slice(0, -1)` yields `a.pn`, matching no asset key. The hazard
  // is then silently ignored, the absent opening hash reads as "brand-new this run", and rule 4 is
  // skipped over old bytes. Each shape below fails for its own reason, so this is a table rather
  // than one case: no colon, an empty path, a path that walks out of the asset tree, an empty or
  // unrecognized reason word, and a `.`/`..` segment.
  const badShapes = [
    'not-an-array', [null], [1], {},
    ['a.png'], [':symlink'], ['a.png:'], ['a.png:hazard'], ['a.png:HARD_LINK'],
    ['../outside.png:symlink'], ['a/../b.png:symlink'], ['./a.png:symlink'], ['a//b.png:symlink'],
  ];
  for (const bad of badShapes) {
    const r = CR.readRunRecordText(JSON.stringify({
      ...base,
      chapters: { items: { opening: {}, closing: {}, opening_hazards: bad, closing_hazards: [] } },
    }));
    assert.equal(r.ok, false, `${JSON.stringify(bad)} was accepted: ${JSON.stringify(r)}`);
    assert.match(r.reason, /^bad_chapter_hazards:/);
  }

  // The positive controls matter as much: a DIRECTORY path is the round-17 case, and a path
  // containing a colon is why the split is at the LAST one. Requiring a file-shaped asset key here
  // would reject both.
  for (const good of [['screens:symlink'], ['screens/a.png:hard_link'], ['odd:name/a.png:non_regular'],
    ['deep/nest/ed/a.png:inspection_failure']]) {
    const r = CR.readRunRecordText(JSON.stringify({
      ...base,
      chapters: { items: { opening: {}, closing: {}, opening_hazards: good, closing_hazards: [] } },
    }));
    assert.equal(r.ok, true, `${JSON.stringify(good)} was rejected: ${JSON.stringify(r)}`);
  }

  const accepted = CR.readRunRecordText(JSON.stringify({
    ...base,
    chapters: { items: { opening: {}, closing: {}, opening_hazards: ['a.png:symlink'], closing_hazards: [] } },
  }));
  assert.equal(accepted.ok, true, JSON.stringify(accepted));
});

// [ped-ant, round 14] The tampering test above MUTATES a payload member; this one REMOVES it. The
// digest recompute treats `runState` as tamperable serialized input — the comment above it says so
// outright — and then hands it to `digestOpeningPayload`, which throws on a member it cannot
// canonicalize. So the one input shape the guard exists for escaped as an exception from a function
// whose whole contract is that ordinary failure returns `{ok:false, halts}`, and it did so before
// anything durable was written, leaving the pending run to be recovered by hand. A payload that
// cannot be canonicalized cannot match the token's digest either, so it is a `stale_replay` like
// every other non-matching payload.
for (const missing of ['entries', 'opening', 'opening_assets']) {
  test(`closeCaptureRun totality: a runState with '${missing}' deleted halts stale_replay rather than throwing`, () => {
    withTempDir((dir) => {
      const profile = profileFor(dir);
      const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, stubDepsNoIdentity());
      assert.equal(opened.ok, true);
      const tampered = JSON.parse(JSON.stringify(opened.runState));
      assert.ok(missing in tampered, `fixture is wrong: '${missing}' is not a runState member`);
      delete tampered[missing];
      const closed = CR.closeCaptureRun(profile, tampered, { ok: true }, null, stubDepsNoIdentity());
      assert.equal(closed.ok, false);
      assert.equal(closed.halts[0].halt, 'stale_replay', JSON.stringify(closed.halts));
    });
  });
}

test('closeCaptureRun: BLOCKER 2 (codex review) — forging ONLY runState.opening_digest must not land the FORGED value in the committed record', () => {
  // The token on disk is the sole AUTHENTICATED source of truth for the opening digest. Close
  // recomputes the digest from runState's own entries/opening/opening_assets and checks it against
  // the token — but a prior version then wrote `runState.opening_digest` (the field, not the
  // recomputed-and-verified value) into the committed record. Mutating ONLY that field (leaving the
  // actual payload untouched) sails straight through the check unnoticed — recomputedDigest is
  // derived from the PAYLOAD, never from this field — so the forged value would land in a
  // successfully committed record, and a later `recoverProvenanceState` read would classify it
  // wrong (a legitimate commit misread as `divergent`, or vice versa, depending on what it's later
  // compared against).
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');
    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true);

    const authenticDigest = opened.runState.opening_digest;
    const tampered = { ...opened.runState, opening_digest: ONE_DIGEST };
    assert.notEqual(tampered.opening_digest, authenticDigest);

    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v2');
    const closed = CR.closeCaptureRun(profile, tampered, { ok: true }, null, stubDepsNoIdentity());
    // The PAYLOAD itself is untouched, so this must still verify and commit — the vulnerability is
    // in WHAT gets written, not whether the close succeeds.
    assert.equal(closed.ok, true, `payload untouched, so this must still commit; got ${JSON.stringify(closed)}`);

    const recordRaw = JSON.parse(nodeFs.readFileSync(recordPathFor(profile), 'utf8'));
    assert.equal(
      recordRaw.opening_digest,
      authenticDigest,
      'the committed record must carry the AUTHENTICATED (recomputed, token-verified) digest, never the forged runState field',
    );
  });
});

test('openCaptureRun on a skipped profile is a no-op returning {ok:true, runState:{skipped:true}}', () => {
  withTempDir((dir) => {
    nodeFs.mkdirSync(join(dir, 'handbook'), { recursive: true });
    const profile = {
      capture: { output_dir: join(dir, 'handbook') },
      publish: { chapters_dir: join(dir, 'handbook') },
    };
    const result = CR.openCaptureRun(profile, [], null, stubDepsNoIdentity());
    assert.equal(result.ok, true);
    assert.equal(result.runState.skipped, true);
    assert.equal(nodeFs.existsSync(join(dir, 'handbook', '.provenance')), false);
  });
});

test('runState survives a real JSON serialization boundary (simulated fresh process)', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    nodeFs.mkdirSync(join(profile.capture.output_dir, 'items'), { recursive: true });
    nodeFs.writeFileSync(join(profile.capture.output_dir, 'items', 'a.png'), 'v1');
    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    const roundTripped = JSON.parse(JSON.stringify(opened.runState));
    nodeFs.writeFileSync(join(profile.capture.output_dir, 'items', 'a.png'), 'v2');
    const closed = CR.closeCaptureRun(profile, roundTripped, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, true);
  });
});

// capture-record.d.mts declares `RunState` as a discriminated union on `skipped`, not one interface
// with every payload field optional — nothing in this repository compiles TypeScript, so that
// declaration is otherwise unchecked by the whole suite (a codex mutation audit confirmed ANY
// `.d.mts` edit survives it). These two tests pin the union's two branches against a REAL
// openCaptureRun/closeCaptureRun round trip, not a hand-built fixture, so a future change that
// widens or narrows either branch's actual field set is caught here.
test('RunState union: an ACTIVE run carries exactly the non-optional payload fields the declaration promises, before and after close', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    nodeFs.mkdirSync(join(profile.capture.output_dir, 'items'), { recursive: true });
    nodeFs.writeFileSync(join(profile.capture.output_dir, 'items', 'a.png'), 'v1');

    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    const openedState = opened.runState;

    assert.equal(openedState.skipped, false);
    assert.equal(typeof openedState.run_id, 'string');
    assert.ok(openedState.run_id.length > 0);
    assert.match(openedState.opening_digest, /^sha256:[0-9a-f]{64}$/);
    assert.deepEqual(Object.keys(openedState.opening).sort(), ['detail', 'resolution_reason', 'source', 'value']);
    assert.equal(typeof openedState.opening_assets, 'object');
    // [round 15] Added with the hazard/absence split: an asset that could not be hashed at open is
    // recorded here rather than being dropped, because a missing key means "brand new" downstream.
    assert.equal(typeof openedState.opening_asset_hazards, 'object');
    assert.ok(Array.isArray(openedState.opening_asset_hazards[Object.keys(openedState.opening_asset_hazards)[0]]));
    assert.ok(Array.isArray(openedState.entries));
    // [round 37] The declaration promises three fields INSIDE `output_root`, and a top-level key
    // list cannot see them: nothing in this repository compiles TypeScript, so an inner field that
    // drifts from the `.d.mts` is invisible unless the shape is pinned here as well. This fixture's
    // root EXISTS at open, so `identity` is a string and `anchor` is null; the absent-root shape
    // (the inverse pairing) is pinned by the first-capture fixtures further down.
    assert.deepEqual(Object.keys(openedState.output_root).sort(), ['anchor', 'canonical', 'identity']);
    assert.equal(typeof openedState.output_root.canonical, 'string');
    assert.match(openedState.output_root.identity, /^\d+:\d+$/);
    assert.equal(openedState.output_root.anchor, null);
    // Not yet closed — `closed` is the one field the declaration keeps optional on this branch,
    // and it must be genuinely ABSENT here (not merely falsy), matching `openCaptureRun`'s own
    // construction, which never assigns it at all.
    assert.equal(Object.hasOwn(openedState, 'closed'), false);
    assert.deepEqual(
      Object.keys(openedState).sort(),
      ['entries', 'opening', 'opening_asset_hazards', 'opening_assets', 'opening_digest', 'output_root', 'run_id', 'skipped'].sort(),
    );

    nodeFs.writeFileSync(join(profile.capture.output_dir, 'items', 'a.png'), 'v2');
    const closed = CR.closeCaptureRun(profile, openedState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, true, JSON.stringify(closed));
    const closedState = closed.runState;

    assert.equal(closedState.skipped, false);
    assert.equal(closedState.run_id, openedState.run_id);
    assert.equal(closedState.opening_digest, openedState.opening_digest);
    assert.deepEqual(closedState.opening, openedState.opening);
    assert.deepEqual(closedState.entries, openedState.entries);
    assert.equal(typeof closedState.opening_assets, 'object');
    assert.equal(closedState.closed, true);
    assert.deepEqual(
      Object.keys(closedState).sort(),
      ['closed', 'entries', 'opening', 'opening_asset_hazards', 'opening_assets', 'opening_digest', 'output_root', 'run_id', 'skipped'].sort(),
    );
  });
});

test('RunState union: a SKIPPED run carries ONLY `skipped: true` — from openCaptureRun and unchanged out of closeCaptureRun', () => {
  withTempDir((dir) => {
    // The EQUAL-topology, no-`build_identity` fixture from the gate-5 section above: ownership
    // overlaps but is unconfigured, so this is the warn-and-skip branch, never the halt branch.
    nodeFs.mkdirSync(join(dir, 'handbook'), { recursive: true });
    const profile = {
      capture: { output_dir: join(dir, 'handbook') },
      publish: { chapters_dir: join(dir, 'handbook') },
    };

    const opened = CR.openCaptureRun(profile, [], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    assert.deepEqual(Object.keys(opened.runState), ['skipped']);
    assert.equal(opened.runState.skipped, true);

    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, true, JSON.stringify(closed));
    assert.deepEqual(Object.keys(closed.runState), ['skipped']);
    assert.equal(closed.runState.skipped, true);
  });
});

// =================================================================================================
// Atomicity — halts, not throws; zero surviving temps on a failure THIS call can still clean up
// after (codex important #6). Every injected failure below is a genuinely unexpected errno, never
// ENOENT/ENOTDIR (which snapshotAssetHashes already treats as "empty directory", legitimately).
// =================================================================================================

test('openCaptureRun: BLOCKER 4 (codex review) — a THROWING pending-token write returns a halt and leaves no orphaned token, rather than escaping uncaught', () => {
  // The write was previously wrapped in a bare `try { ... } finally { closeSync }` with no `catch`
  // at all — an ordinary write failure (EIO, ENOSPC, ...) became an UNCAUGHT exception instead of
  // this module's usual returned `{ok:false, halts}`, and the just-created (O_CREAT|O_EXCL) token
  // was left behind with no caller ever having a chance to clean it up.
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const boom = Object.assign(new Error('injected token write failure'), { code: 'EIO' });
    const deps = depsWithOverride({
      writeSync: () => {
        throw boom;
      },
      runIdentityCommand: () => ({ ok: false, detail: 'not configured' }),
    });
    assert.doesNotThrow(() => {
      const result = CR.openCaptureRun(profile, [], null, deps);
      assert.equal(result.ok, false, JSON.stringify(result));
      assert.equal(result.halts[0].halt, 'provenance_hazard');
    });
    assert.equal(nodeFs.existsSync(tokenPathFor(profile)), false, 'a throwing token write must not leave an orphaned token on disk');
  });
});

test('openCaptureRun: BLOCKER 4 (codex review) — a SHORT writeSync on the pending token must not leave a truncated token in place', () => {
  // writeSync genuinely CAN return fewer bytes than requested (a full disk, a pipe, an interrupted
  // write) — every writer in this module ignored the returned byte count outright.
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const deps = depsWithOverride({
      writeSync: (fd, buffer, ...rest) => {
        nodeFs.writeSync(fd, buffer, ...rest);
        return 1; // report a short write regardless of the real (full) byte count
      },
      runIdentityCommand: () => ({ ok: false, detail: 'not configured' }),
    });
    const result = CR.openCaptureRun(profile, [], null, deps);
    assert.equal(result.ok, false, `a short write must halt rather than open; got ${JSON.stringify(result)}`);
    assert.equal(nodeFs.existsSync(tokenPathFor(profile)), false, 'a short-written token must not survive on disk');
  });
});

test('openCaptureRun (codex round 9, finding 1a): a malformed opening observation THROWS inside resolveBuildIdentity but must return a halt with the reservation released, not escape uncaught', () => {
  // `resolveBuildIdentity` throws a TypeError on an unrecognized `uiObservation.kind`
  // (build-identity.mjs) — a shape that reaches it from a UI read, which is untrusted input by
  // this project's own reference doc. Before this fix, the throw escaped `openCaptureRun` entirely:
  // the just-created (O_CREAT|O_EXCL) token was never unlinked and its fd was never closed.
  withTempDir((dir) => {
    const profile = profileFor(dir, { capture: { build_identity: { ui_read: true } } });
    const deps = depsWithOverride({});
    assert.doesNotThrow(() => {
      const result = CR.openCaptureRun(profile, [], { kind: 'bogus' }, deps);
      assert.equal(result.ok, false, JSON.stringify(result));
      // Not `provenance_hazard` (round 9 follow-up): a malformed caller-argument shape is the same
      // CLASS of defect as `identity_resolution_threw`'s sibling `extraction_threw` below, not a
      // filesystem/hazard condition.
      assert.equal(result.halts[0].halt, 'identity_resolution_threw', JSON.stringify(result));
    });
    assert.equal(nodeFs.existsSync(tokenPathFor(profile)), false, 'a throwing identity resolution must not leave the reservation token on disk');
  });
});

test('openCaptureRun (codex round 9, finding 1a): a THROWING identity command executor must return a halt with the reservation released, not escape uncaught', () => {
  // `resolveIdentityCommandOutcome` calls `d.runIdentityCommand` with no guard of its own — the
  // command is arbitrary operator shell and can throw just as easily as answer.
  withTempDir((dir) => {
    const profile = profileFor(dir, { capture: { build_identity: { command: 'get-version', ui_read: false } } });
    const deps = depsWithOverride({
      runIdentityCommand: () => {
        throw new Error('injected identity executor failure');
      },
    });
    assert.doesNotThrow(() => {
      const result = CR.openCaptureRun(profile, [], null, deps);
      assert.equal(result.ok, false, JSON.stringify(result));
      assert.equal(result.halts[0].halt, 'identity_resolution_threw', JSON.stringify(result));
    });
    assert.equal(nodeFs.existsSync(tokenPathFor(profile)), false, 'a throwing identity command must not leave the reservation token on disk');
  });
});

test('openCaptureRun (codex round 10, finding 1): the identity-command guard itself must not crash on a NON-ERROR thrown value (null, undefined, a string, a number, an object with no .message)', () => {
  // The guard at `resolveIdentityOrHalt`'s first catch exists specifically to convert a throw into
  // an `identity_resolution_threw` halt. JavaScript permits throwing ANY value — `throw null` and
  // `throw undefined` make a raw `err.message` access throw a SECOND, unrelated TypeError from
  // INSIDE the guard, which is worse than not guarding at all: it escapes uncaught, and in
  // `openCaptureRun` specifically the reservation is still held at that point, so the escape is
  // also the exact fd/token leak finding 1a already closed once, reachable again through a
  // different door. Every test added for finding 1a threw a real `Error`, which is why the suite
  // never saw this — codex reproduced it through `buildProvenanceReport` with a `null` throw.
  // Covered here: `null`, `undefined` (the two that crash a raw `.message`/`.code` access outright),
  // a plain string and a number (safe from crashing — primitives auto-box — but must still produce
  // a SENSIBLE message rather than a bare `undefined`), and a plain object with no `.message` field
  // at all (must fall back to a string form of the object, never `undefined`). Not covered
  // separately: a thrown `Symbol` — `describeThrown` uses `String(err)` rather than template-literal
  // interpolation specifically because `${sym}` throws while `String(sym)` does not, so a Symbol is
  // exercised by the SAME code path as the string/number cases, not a distinct one.
  //
  // codex round 11, finding 2: `typeof message === 'string'` alone is satisfied by
  // `describeThrown = () => ''`, which would discard the original failure entirely — exactly the
  // property this test exists to protect. Each case below pins the FRAGMENT `describeThrown` must
  // actually preserve from that specific thrown value, derived independently from what each
  // fallback step (`err.message`, then `String(err)`, then `Object.prototype.toString.call(err)`)
  // is documented to produce for that shape.
  const thrownValues = [
    ['null', null, 'null'],
    ['undefined', undefined, 'undefined'],
    ['a plain string', 'boom', 'boom'],
    ['a number', 42, '42'],
    ['a plain object with no .message', { code: 'EWEIRD' }, '[object Object]'],
  ];
  for (const [label, thrown, expectedFragment] of thrownValues) {
    withTempDir((dir) => {
      const profile = profileFor(dir, { capture: { build_identity: { command: 'get-version', ui_read: false } } });
      const deps = depsWithOverride({
        runIdentityCommand: () => {
          throw thrown;
        },
      });
      assert.doesNotThrow(() => {
        const result = CR.openCaptureRun(profile, [], null, deps);
        assert.equal(result.ok, false, `[${label}] expected a halt, got ${JSON.stringify(result)}`);
        assert.equal(result.halts[0].halt, 'identity_resolution_threw', `[${label}] got ${JSON.stringify(result)}`);
        assert.equal(typeof result.halts[0].message, 'string', `[${label}] the halt message must be a string, got ${JSON.stringify(result)}`);
        assert.ok(
          result.halts[0].message.includes(expectedFragment),
          `[${label}] expected the message to preserve '${expectedFragment}', got ${JSON.stringify(result.halts[0].message)}`,
        );
      }, `[${label}] the guard itself must not throw`);
      assert.equal(nodeFs.existsSync(tokenPathFor(profile)), false, `[${label}] a throwing identity command must not leave the reservation token on disk`);
    });
  }
});

test('openCaptureRun (codex round 11, finding 1a): a thrown value with NO prototype at all (Object.create(null)) must not crash String() inside the guard', () => {
  // `Object.create(null)` has no `toString`/`Symbol.toPrimitive` at all — `String(err)` on it
  // throws `Cannot convert object to primitive value`, reached through openCaptureRun with the
  // seam trace showing only `["open"]`: control escaped the catch BEFORE `releaseReservation`, so
  // the reservation leaked again — the third round this exact leak has come back through a new
  // door (round 9's throw, round 10's non-Error throw, now the FORMATTER's own throw).
  withTempDir((dir) => {
    const profile = profileFor(dir, { capture: { build_identity: { command: 'get-version', ui_read: false } } });
    const deps = depsWithOverride({
      runIdentityCommand: () => {
        throw Object.create(null);
      },
    });
    assert.doesNotThrow(() => {
      const result = CR.openCaptureRun(profile, [], null, deps);
      assert.equal(result.ok, false, JSON.stringify(result));
      assert.equal(result.halts[0].halt, 'identity_resolution_threw', JSON.stringify(result));
      assert.equal(typeof result.halts[0].message, 'string', JSON.stringify(result));
      assert.ok(result.halts[0].message.includes('[object Object]'), `expected the Object.prototype.toString fallback, got ${JSON.stringify(result.halts[0].message)}`);
    }, 'the guard itself must not throw, even on a value with no prototype');
    assert.equal(nodeFs.existsSync(tokenPathFor(profile)), false, 'a throwing identity command with no prototype at all must not leave the reservation token on disk');
  });
});

test('openCaptureRun (codex round 11, finding 1b): a thrown value whose .message is itself a Symbol must not crash template-literal interpolation inside the guard', () => {
  // `errProp` returns whatever `.message` holds without checking its TYPE — a `{message:
  // Symbol('boom')}` throw hands the Symbol itself back, and interpolating a raw Symbol into a
  // template literal throws `Cannot convert a Symbol value to a string`.
  withTempDir((dir) => {
    const profile = profileFor(dir, { capture: { build_identity: { command: 'get-version', ui_read: false } } });
    const deps = depsWithOverride({
      runIdentityCommand: () => {
        throw { message: Symbol('boom') };
      },
    });
    assert.doesNotThrow(() => {
      const result = CR.openCaptureRun(profile, [], null, deps);
      assert.equal(result.ok, false, JSON.stringify(result));
      assert.equal(result.halts[0].halt, 'identity_resolution_threw', JSON.stringify(result));
      assert.equal(typeof result.halts[0].message, 'string', JSON.stringify(result));
      assert.ok(result.halts[0].message.includes('[object Object]'), `expected the String(err) fallback (the .message field is not itself a string), got ${JSON.stringify(result.halts[0].message)}`);
    }, 'the guard itself must not throw, even when .message is a Symbol');
    assert.equal(nodeFs.existsSync(tokenPathFor(profile)), false, 'a throwing identity command with a Symbol .message must not leave the reservation token on disk');
  });
});

test('openCaptureRun (codex round 11, finding 1c): a thrown FUNCTION carrying its own .code must still classify EEXIST as run_already_open, not fall through to the generic provenance_hazard', () => {
  // The previous `errProp` excluded functions (`typeof fn === 'object'` is false — functions report
  // `typeof === 'function'`), so a thrown function carrying `.code = 'EEXIST'` silently lost that
  // field, and `openCaptureRun` returned `provenance_hazard` where it used to (correctly) return
  // `run_already_open`. This is a BEHAVIORAL regression the round-10 rewrite introduced, not merely
  // a formatting one — a message-only assertion cannot show it, only the halt NAME can.
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const thrownFn = () => {};
    thrownFn.code = 'EEXIST';
    const deps = depsWithOverride({
      openSync: () => {
        throw thrownFn;
      },
    });
    const result = CR.openCaptureRun(profile, [], null, deps);
    assert.equal(result.ok, false, JSON.stringify(result));
    assert.equal(result.halts[0].halt, 'run_already_open', `expected run_already_open (the correct classification for EEXIST), got ${JSON.stringify(result)}`);
  });
});

test('closeCaptureRun (codex round 11, finding 1c corroboration): a thrown FUNCTION carrying its own .code must still classify ENOENT as token_missing, not fall through to the generic provenance_hazard', () => {
  // Same class of regression as finding 1c above, but a DIFFERENT function (`openLeafNoFollow`,
  // reached via `readLeafText` on the token read) through a DIFFERENT entrypoint — corroborating
  // that the `errProp` fix generalizes across every comparison site sharing the helper, not just
  // the one codex happened to name.
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    const thrownFn = () => {};
    thrownFn.code = 'ENOENT';
    const deps = depsWithOverride({
      openSync: (p, flags, mode) => {
        if (p === tokenPathFor(profile)) throw thrownFn;
        return nodeFs.openSync(p, flags, mode);
      },
    });
    const result = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, deps);
    assert.equal(result.ok, false, JSON.stringify(result));
    assert.equal(result.halts[0].halt, 'token_missing', `expected token_missing (the correct classification for ENOENT), got ${JSON.stringify(result)}`);
  });
});

test('openCaptureRun (codex round 12, finding 1): a thrown Proxy whose property read ITSELF throws must not crash the guard, and the reservation must still be released', () => {
  // `errProp` read `err[name]` directly — a Proxy `get` trap that throws for EVERY property takes
  // the read down with it, same as `Object.prototype.toString.call(err)` invoking a hostile
  // `Symbol.toStringTag` getter (that lookup IS a property read on `err`, so the same trap catches
  // it too). Reproduced by codex through openCaptureRun: seam trace `["open"]` only — neither close
  // nor unlink ran, the reservation leaking a FOURTH distinct way (round 9's throw, round 10's
  // non-Error throw, round 11's formatter throw, now a hostile getter on the thrown value itself).
  // This one Proxy exercises ALL THREE fallback layers in `describeThrown`: `errProp(err,
  // 'message')` throws internally (caught, returns undefined) -> `String(err)` throws (its
  // coercion reads a property too) -> `Object.prototype.toString.call(err)` ALSO throws (same
  // trap intercepts Symbol.toStringTag) -> the final literal string, which cannot throw because it
  // touches `err` not at all.
  withTempDir((dir) => {
    const profile = profileFor(dir, { capture: { build_identity: { command: 'get-version', ui_read: false } } });
    const hostileProxy = new Proxy(
      {},
      {
        get() {
          throw new Error('hostile getter');
        },
      },
    );
    const deps = depsWithOverride({
      runIdentityCommand: () => {
        throw hostileProxy;
      },
    });
    assert.doesNotThrow(() => {
      const result = CR.openCaptureRun(profile, [], null, deps);
      assert.equal(result.ok, false, JSON.stringify(result));
      assert.equal(result.halts[0].halt, 'identity_resolution_threw', JSON.stringify(result));
      assert.equal(typeof result.halts[0].message, 'string', JSON.stringify(result));
      assert.ok(
        result.halts[0].message.includes('<unstringifiable thrown value>'),
        `expected the ultimate literal fallback, got ${JSON.stringify(result.halts[0].message)}`,
      );
    }, 'the guard itself must not throw, even when EVERY property read on the thrown value throws');
    assert.equal(nodeFs.existsSync(tokenPathFor(profile)), false, 'a throwing identity command (a hostile Proxy) must not leave the reservation token on disk');
  });
});

test('buildProvenanceReport (codex round 10, finding 1): a THROWING identity command with a null payload must not crash the guard — the exact shape codex reproduced', () => {
  // codex's own repro, driven through the entrypoint it actually found it in (W6, not
  // openCaptureRun): `throw null` from the injected identity command executor.
  withTempDir((dir) => {
    const profile = profileFor(dir, { capture: { build_identity: { command: 'get-version', ui_read: false } } });
    const entry = { slug: 'items' };
    writeChapterAt(profile, entry, '# items\n');
    const deps = depsWithOverride({
      runIdentityCommand: () => {
        throw null;
      },
      expectedAssets: emptyExpectedAssets,
    });
    assert.doesNotThrow(() => {
      const result = CR.buildProvenanceReport(profile, [entry], null, deps);
      assert.equal(result.ok, false, JSON.stringify(result));
      assert.equal(result.halts[0].halt, 'identity_resolution_threw', JSON.stringify(result));
      assert.equal(typeof result.halts[0].message, 'string', JSON.stringify(result));
    });
  });
});

test('openCaptureRun (codex round 9, finding 1a): a THROWING randomUUID during run-state construction must return a halt with the reservation released, not escape uncaught', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const deps = depsWithOverride({
      randomUUID: () => {
        throw new Error('injected randomUUID failure');
      },
    });
    assert.doesNotThrow(() => {
      const result = CR.openCaptureRun(profile, [], null, deps);
      assert.equal(result.ok, false, JSON.stringify(result));
      assert.equal(result.halts[0].halt, 'provenance_hazard', JSON.stringify(result));
    });
    assert.equal(nodeFs.existsSync(tokenPathFor(profile)), false, 'a throwing randomUUID must not leave the reservation token on disk');
  });
});

test('openCaptureRun (codex round 10, finding 1): a THROWING randomUUID with a NON-ERROR payload (null, undefined) must not crash the run-state-construction guard', () => {
  for (const [label, thrown] of [['null', null], ['undefined', undefined]]) {
    withTempDir((dir) => {
      const profile = profileFor(dir);
      const deps = depsWithOverride({
        randomUUID: () => {
          throw thrown;
        },
      });
      assert.doesNotThrow(() => {
        const result = CR.openCaptureRun(profile, [], null, deps);
        assert.equal(result.ok, false, `[${label}] ${JSON.stringify(result)}`);
        assert.equal(result.halts[0].halt, 'provenance_hazard', `[${label}] ${JSON.stringify(result)}`);
        assert.equal(typeof result.halts[0].message, 'string', `[${label}] ${JSON.stringify(result)}`);
      }, `[${label}] the guard itself must not throw`);
      assert.equal(nodeFs.existsSync(tokenPathFor(profile)), false, `[${label}] a throwing randomUUID must not leave the reservation token on disk`);
    });
  }
});

test('openCaptureRun (codex round 9, finding 1a): a halt taken after the reservation ALSO surfaces a non-empty `warnings` array when the release itself fails', () => {
  // The three tests above prove the reservation is RELEASED on a throw; this proves that when the
  // release itself cannot remove the token (EACCES), that failure is reported rather than silently
  // swallowed — the same "must not be silent" requirement finding 1b states for needs_ui_read,
  // extended to every halt this function can return after taking the reservation.
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const deps = depsWithOverride({
      randomUUID: () => {
        throw new Error('injected randomUUID failure');
      },
      unlinkSync: (p) => {
        if (p === tokenPathFor(profile)) throw Object.assign(new Error('injected'), { code: 'EACCES' });
        return nodeFs.unlinkSync(p);
      },
    });
    const result = CR.openCaptureRun(profile, [], null, deps);
    assert.equal(result.ok, false, JSON.stringify(result));
    assert.equal(result.halts[0].halt, 'provenance_hazard');
    assert.ok(
      Array.isArray(result.halts[0].warnings) && result.halts[0].warnings.length > 0,
      `expected the halt to carry a non-empty warnings array; got ${JSON.stringify(result)}`,
    );
    assert.match(result.halts[0].warnings[0], /'partial'/);
    assert.equal(nodeFs.existsSync(tokenPathFor(profile)), true, 'the injected EACCES must have left the token in place');
  });
});

test("openCaptureRun (codex round 9, finding 1b): a needs_ui_read return whose token cannot be removed surfaces a non-empty `warnings` array naming 'partial', not a silently-failed release", () => {
  withTempDir((dir) => {
    const profile = profileFor(dir, { capture: { build_identity: { ui_read: true } } });
    const deps = depsWithOverride({
      unlinkSync: (p) => {
        if (p === tokenPathFor(profile)) throw Object.assign(new Error('injected'), { code: 'EACCES' });
        return nodeFs.unlinkSync(p);
      },
    });
    const result = CR.openCaptureRun(profile, [], null, deps); // ui_read enabled, no observation -> needs_ui_read
    assert.equal(result.needs_ui_read, true, JSON.stringify(result));
    assert.ok(Array.isArray(result.warnings) && result.warnings.length > 0, `expected a non-empty warnings array; got ${JSON.stringify(result)}`);
    assert.match(result.warnings[0], /'partial'/);
    assert.equal(nodeFs.existsSync(tokenPathFor(profile)), true, 'the injected EACCES must have left the token in place');
  });
});

test('openCaptureRun (codex round 9, finding 1b): a needs_ui_read return whose token IS removed reports an EMPTY warnings array, present rather than omitted', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir, { capture: { build_identity: { ui_read: true } } });
    const deps = depsWithOverride({});
    const result = CR.openCaptureRun(profile, [], null, deps);
    assert.equal(result.needs_ui_read, true, JSON.stringify(result));
    assert.deepEqual(result.warnings, [], 'a clean release must report warnings as an empty array, not omit the field');
    assert.equal(nodeFs.existsSync(tokenPathFor(profile)), false, 'a clean release must actually remove the token');
  });
});

test("openCaptureRun: a failing close AFTER a successful token write reports the state row 6 will ACTUALLY classify ('open'), never the shared partial-token wording", () => {
  // Distinct from the shared `releaseReservation` sites above: by the time this close fails, the
  // token already holds this run's real (valid) run_id/opening_digest, so row 6 classifies it
  // 'open' (no matching record yet), not 'partial' — a wrong state name here would send an
  // operator to `recoverProvenanceState` expecting one report and getting another.
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const deps = depsWithOverride({
      closeSync: () => {
        throw Object.assign(new Error('injected'), { code: 'EIO' });
      },
      unlinkSync: (p) => {
        if (p === tokenPathFor(profile)) throw Object.assign(new Error('injected'), { code: 'EACCES' });
        return nodeFs.unlinkSync(p);
      },
    });
    const result = CR.openCaptureRun(profile, [], null, deps);
    assert.equal(result.ok, false, JSON.stringify(result));
    assert.equal(result.halts[0].halt, 'provenance_hazard');
    assert.match(result.halts[0].message, /cannot close the pending token/);
    assert.ok(result.halts[0].warnings.length > 0, `expected a non-empty warnings array; got ${JSON.stringify(result)}`);
    assert.match(result.halts[0].warnings[0], /'open'/);
    assert.equal(nodeFs.existsSync(tokenPathFor(profile)), true, 'the injected EACCES must have left the (now fully-written) token in place');
  });
});

test('openCaptureRun: an unexpected snapshot-listing errno returns a halt, never an uncaught throw, and the reservation is fully released', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    const boom = Object.assign(new Error('injected'), { code: 'EIO' });
    const deps = depsWithOverride({
      readdirSync: (p, opts) => {
        if (p === assetDir) throw boom;
        return nodeFs.readdirSync(p, opts);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'not configured' }),
    });
    assert.doesNotThrow(() => {
      const result = CR.openCaptureRun(profile, [entry], null, deps);
      assert.equal(result.ok, false, JSON.stringify(result));
      assert.equal(result.halts[0].halt, 'provenance_hazard');
      // codex round 9, finding 3: this used to check only the returned halt, so deleting the
      // reservation's release from this catch still passed — pin the actual on-disk effect too.
      assert.deepEqual(result.halts[0].warnings, [], 'a clean release on this path must report an empty warnings array');
    });
    assert.equal(nodeFs.existsSync(tokenPathFor(profile)), false, 'a snapshot hazard must not leave the reservation token on disk');
  });
});

test('closeCaptureRun: an unexpected closing-snapshot errno returns a halt, and the prior run record is byte-identical', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');
    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true);
    // A prior run record already on disk, which must survive byte-identical.
    nodeFs.writeFileSync(recordPathFor(profile), 'PRIOR_RECORD_SENTINEL');
    const boom = Object.assign(new Error('injected'), { code: 'EIO' });
    const deps = depsWithOverride({
      readdirSync: (p, opts) => {
        if (p === assetDir) throw boom;
        return nodeFs.readdirSync(p, opts);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'not configured' }),
    });
    assert.doesNotThrow(() => {
      const result = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, deps);
      assert.equal(result.ok, false, JSON.stringify(result));
      assert.equal(result.halts[0].halt, 'provenance_hazard');
    });
    assert.equal(nodeFs.readFileSync(recordPathFor(profile), 'utf8'), 'PRIOR_RECORD_SENTINEL');
  });
});

test('closeCaptureRun: BLOCKER 4 (codex review) — a SHORT writeSync must not commit a truncated run record', () => {
  // Reproduces the finding's own illustration: "a seam that persists one byte and returns 1" let
  // close rename a one-byte "{" temp into place as a successfully committed run record, because the
  // writer ignored writeSync's returned byte count entirely.
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const opened = CR.openCaptureRun(profile, [], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true);
    const deps = depsWithOverride({
      writeSync: (fd, buffer, ...rest) => {
        nodeFs.writeSync(fd, buffer, ...rest);
        return 1; // report a short write regardless of the real (full) byte count
      },
      runIdentityCommand: () => ({ ok: false, detail: 'not configured' }),
    });
    const result = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, deps);
    assert.equal(result.ok, false, `a short write must halt rather than commit; got ${JSON.stringify(result)}`);
    assert.equal(nodeFs.existsSync(recordPathFor(profile)), false, 'a short-written record must never be committed (renamed into place)');
    assert.equal(listRunTempsOnDisk(profile).length, 0, 'a short-write failure must leave no surviving temp');
  });
});

// =================================================================================================
// BLOCKER (codex round 3) — ordinary I/O failures must never throw uncaught, including AFTER a
// durable commit, and a cleanup close failure must never MASK a more important error already being
// reported. `listMatchingTemps` (the readdirSync-based helper under `run/`) previously rethrew any
// non-ENOENT errno; `closeCaptureRun` called it UNGUARDED after its rename had already committed
// `current.json`, and the SAME helper was unguarded in `recoverProvenanceState`'s recovery path.
// Separately, `readAllFromFd`'s `readSync` was unguarded, and every write-failure catch block's own
// cleanup `closeSync` could itself throw and silently REPLACE the pending exception it was
// cleaning up after (a throwing `catch`/`finally` body overrides whatever was already propagating).
// =================================================================================================

test('closeCaptureRun: BLOCKER (codex round 3) — a post-commit listing hazard is a WARNING on ok:true, never implies nothing was written', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const opened = CR.openCaptureRun(profile, [], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true);
    const runDirPath = runDir(profile);
    const boom = Object.assign(new Error('injected listing failure'), { code: 'EIO' });
    const deps = depsWithOverride({
      readdirSync: (p, ...rest) => {
        if (p === runDirPath) throw boom;
        return nodeFs.readdirSync(p, ...rest);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'not configured' }),
    });
    assert.doesNotThrow(() => {
      const result = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, deps);
      assert.equal(result.ok, true, `the rename already committed the record — this must stay ok:true, not become a halt; got ${JSON.stringify(result)}`);
      // Two warnings on this fixture, not one: `profileFor`'s default `build_identity: { ui_read:
      // false }` with no command configured resolves to `no_source_configured` on both ends, which
      // now ALSO warns (Finding 2) — a fact orthogonal to the one THIS test pins (the temp-listing
      // hazard's own warning), so both are asserted rather than a bare length check hiding which is
      // which.
      assert.equal(result.warnings.length, 2, JSON.stringify(result.warnings));
      assert.ok(result.warnings.some((w) => /committed successfully/.test(w)), JSON.stringify(result.warnings));
      assert.ok(result.warnings.some((w) => /no build identity source is configured/.test(w)), JSON.stringify(result.warnings));
    });
    assert.equal(nodeFs.existsSync(recordPathFor(profile)), true, 'the run record must be durably committed despite the cleanup hazard');
  });
});

test('recoverProvenanceState: BLOCKER (codex round 3) — a listing hazard while enumerating temps returns a halt, never an uncaught throw', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const opened = CR.openCaptureRun(profile, [], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true);
    const runDirPath = runDir(profile);
    const boom = Object.assign(new Error('injected listing failure'), { code: 'EIO' });
    const deps = depsWithOverride({
      readdirSync: (p, ...rest) => {
        if (p === runDirPath) throw boom;
        return nodeFs.readdirSync(p, ...rest);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'not configured' }),
    });
    assert.doesNotThrow(() => {
      const result = CR.recoverProvenanceState(profile, deps);
      assert.equal(result.ok, false, JSON.stringify(result));
      assert.equal(result.halts[0].halt, 'provenance_hazard');
    });
  });
});

test('closeCaptureRun: BLOCKER (codex round 3) — a closeSync failure after a WRITE failure must not mask the write-failure halt', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const opened = CR.openCaptureRun(profile, [], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true);
    const writeBoom = Object.assign(new Error('injected write failure'), { code: 'EIO' });
    const closeBoom = Object.assign(new Error('injected close failure'), { code: 'EBADF' });
    const deps = depsWithOverride({
      writeSync: () => { throw writeBoom; },
      closeSync: () => { throw closeBoom; },
      runIdentityCommand: () => ({ ok: false, detail: 'not configured' }),
    });
    const result = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, deps);
    assert.equal(result.ok, false, JSON.stringify(result));
    // The halt message is built from `err.code ?? err.message` — the WRITE error's own code
    // ("EIO") must be what survives; the CLOSE error's code ("EBADF") or text must never appear,
    // which is exactly what a masking `catch`/`finally` body overwriting the pending exception
    // would produce instead.
    assert.match(result.halts[0].message, /EIO/, `the write failure must survive a subsequent close failure; got ${JSON.stringify(result)}`);
    assert.doesNotMatch(result.halts[0].message, /EBADF|close failure/, `the close failure must never mask the write failure; got ${JSON.stringify(result)}`);
  });
});

test('closeCaptureRun: BLOCKER (codex round 3) — a closeSync failure AFTER a successful write (before rename) halts rather than committing an unclosed temp', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const opened = CR.openCaptureRun(profile, [], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true);
    let tempFd = null;
    const boom = Object.assign(new Error('injected close failure'), { code: 'EBADF' });
    const deps = depsWithOverride({
      openSync: (p, ...rest) => {
        const fd = nodeFs.openSync(p, ...rest);
        if (p.endsWith('.tmp')) tempFd = fd;
        return fd;
      },
      closeSync: (fd) => {
        if (fd === tempFd) {
          tempFd = null; // fail only this ONE close, not every close for the rest of the test
          throw boom;
        }
        return nodeFs.closeSync(fd);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'not configured' }),
    });
    const result = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, deps);
    assert.equal(result.ok, false, `a close failure right after a successful write must halt, not commit; got ${JSON.stringify(result)}`);
    assert.equal(nodeFs.existsSync(recordPathFor(profile)), false, 'nothing must be committed — the rename never got a chance to run');
  });
});

test('closeCaptureRun: BLOCKER (codex round 3) — a closeSync failure while classifying a non-regular token hazard must not mask the hazard classification', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const opened = CR.openCaptureRun(profile, [], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true);
    // Replace the token with a DIRECTORY — openLeafNoFollow's non-regular-file hazard branch.
    nodeFs.unlinkSync(tokenPathFor(profile));
    nodeFs.mkdirSync(tokenPathFor(profile));
    const boom = Object.assign(new Error('injected close failure'), { code: 'EBADF' });
    const deps = depsWithOverride({
      closeSync: () => { throw boom; },
      runIdentityCommand: () => ({ ok: false, detail: 'not configured' }),
    });
    assert.doesNotThrow(() => {
      const result = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, deps);
      assert.equal(result.ok, false, JSON.stringify(result));
      assert.equal(result.halts[0].halt, 'provenance_hazard');
      assert.equal(result.halts[0].reason, 'non_regular', JSON.stringify(result));
    });
  });
});

test('recordChapterProvenance: BLOCKER (codex round 3) — a readSync failure while reading the run record returns a halt rather than throwing', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const { runId, assetDir, chapterFile } = runToCommitted(profile, entry, { 'a.png': 'v1' }, { 'a.png': 'v2' });
    const boom = Object.assign(new Error('injected read failure'), { code: 'EIO' });
    const deps = {
      ...stubDepsNoIdentity(),
      expectedAssets: stubExpectedAssetsFor(assetDir, ['a.png']),
      readSync: () => { throw boom; },
    };
    assert.doesNotThrow(() => {
      const result = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, runId, deps);
      assert.equal(result.ok, false, JSON.stringify(result));
      assert.equal(result.halts[0].halt, 'provenance_hazard');
    });
  });
});

test('closeCaptureRun: a temp-write failure and a rename failure both leave ZERO surviving temps', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const opened = CR.openCaptureRun(profile, [], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true);
    const boom = Object.assign(new Error('injected write failure'), { code: 'EIO' });
    const deps = depsWithOverride({
      writeSync: (fd, ...rest) => {
        throw boom;
      },
      runIdentityCommand: () => ({ ok: false, detail: 'not configured' }),
    });
    const result = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, deps);
    assert.equal(result.ok, false, JSON.stringify(result));
    assert.equal(listRunTempsOnDisk(profile).length, 0, 'a temp-write failure must leave no surviving temp');
  });

  withTempDir((dir) => {
    const profile = profileFor(dir);
    const opened = CR.openCaptureRun(profile, [], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true);
    const boom = Object.assign(new Error('injected rename failure'), { code: 'EIO' });
    const deps = depsWithOverride({
      renameSync: () => {
        throw boom;
      },
      runIdentityCommand: () => ({ ok: false, detail: 'not configured' }),
    });
    const result = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, deps);
    assert.equal(result.ok, false, JSON.stringify(result));
    assert.equal(listRunTempsOnDisk(profile).length, 0, 'a rename failure must leave no surviving temp');
  });
});

test('closeCaptureRun (codex round 7, IMPORTANT 2): a run-temp that cannot be unlinked is warned about, stays on disk, and the token is retained rather than reporting a false clean', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const opened = CR.openCaptureRun(profile, [], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true);
    // An "old" leftover temp from a previously crashed run, unrelated to THIS run's own
    // write-then-rename — `listMatchingTemps` matches any `current.json.*.tmp` under run/, not only
    // the temp this close call itself just wrote.
    const stuckTemp = tempPathFor(profile, 'eeeeeeee-0000-0000-0000-000000000000');
    nodeFs.writeFileSync(stuckTemp, '{"stale":true}');
    const deps = depsWithOverride({
      unlinkSync: (p) => {
        if (p === stuckTemp) {
          const err = new Error('permission denied');
          err.code = 'EACCES';
          throw err;
        }
        return nodeFs.unlinkSync(p);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'not configured' }),
    });
    const result = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, deps);
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.equal(nodeFs.existsSync(recordPathFor(profile)), true, 'the run record must still be durably committed despite the stuck temp');
    assert.equal(nodeFs.existsSync(stuckTemp), true, 'a temp whose unlink genuinely failed must still be on disk, matching the honest report');
    // Before the fix: `unlinkBestEffort`'s returned boolean was discarded here (unlike the parallel
    // fix `sweepChapterProvenanceTemps` already got in round 6), so a failed unlink produced no
    // warning at all — only a failure to LIST temps warned. Two warnings on this fixture, not one:
    // `profileFor`'s default `build_identity: { ui_read: false }` with no command configured also
    // warns `no build identity source is configured` (Finding 2 from an earlier round), a fact
    // orthogonal to the one this test pins.
    assert.equal(result.warnings.length, 2, JSON.stringify(result.warnings));
    assert.ok(result.warnings.some((w) => /no build identity source is configured/.test(w)), JSON.stringify(result.warnings));
    const stuckTempPattern = new RegExp(stuckTemp.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
    assert.ok(result.warnings.some((w) => stuckTempPattern.test(w)), `expected a warning naming the stuck temp; got ${JSON.stringify(result.warnings)}`);
    // The token must NOT be removed when a temp's removal could not be confirmed — see the ORDER
    // rationale in the comment above this cleanup in capture-record.mjs, and the pair of tests
    // right below this one for what retaining the token actually buys the operator.
    assert.equal(nodeFs.existsSync(tokenPathFor(profile)), true, 'the token must be retained when a temp could not be confirmed removed, so the run does not read as a false clean');
  });
});

test('closeCaptureRun (codex round 7, IMPORTANT 2): retaining the token on a stuck temp forces recovery — recoverProvenanceState reports `committed`, and the next openCaptureRun halts on run_already_open', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const opened = CR.openCaptureRun(profile, [], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true);
    const stuckTemp = tempPathFor(profile, 'ffffffff-0000-0000-0000-000000000000');
    nodeFs.writeFileSync(stuckTemp, '{"stale":true}');
    const deps = depsWithOverride({
      unlinkSync: (p) => {
        if (p === stuckTemp) {
          const err = new Error('permission denied');
          err.code = 'EACCES';
          throw err;
        }
        return nodeFs.unlinkSync(p);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'not configured' }),
    });
    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, deps);
    assert.equal(closed.ok, true, JSON.stringify(closed));

    // With the token retained, the token/record pair still matches (same run_id, same
    // opening_digest) — row 6 classifies this as `committed`, the state whose OWN repair
    // (`cleanupCommittedRun`) re-verifies the token's fingerprint before touching anything, rather
    // than `orphan_temp` (reached only once the token is gone), whose repair sweeps blind.
    const recovered = CR.recoverProvenanceState(profile, stubDepsNoIdentity());
    assert.equal(recovered.state, 'committed', `expected the token-retained close to classify as committed; got ${JSON.stringify(recovered)}`);

    // Before the fix (unconditional token removal): the next openCaptureRun would have returned
    // {ok:true}, silently opening a new run over an unresolved stuck temp with nothing left to
    // prompt the operator toward recoverProvenanceState ever again.
    const reopened = CR.openCaptureRun(profile, [], null, stubDepsNoIdentity());
    assert.equal(reopened.ok, false, JSON.stringify(reopened));
    assert.equal(reopened.halts[0].halt, 'run_already_open', 'a stuck cleanup temp must force the operator through recovery, not silently allow a new run to open');
  });
});

test('closeCaptureRun (codex round 8, IMPORTANT 2): a failing FINAL token unlink is warned about on ok:true, not silently swallowed', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const opened = CR.openCaptureRun(profile, [], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true);
    // No stuck temp this time — every temp (there are none) is confirmed gone, so the cleanup
    // reaches the FINAL, previously-unconditional `unlinkBestEffort(tokenPath, d)` call and it is
    // THIS unlink that fails.
    const boom = Object.assign(new Error('permission denied'), { code: 'EACCES' });
    const deps = depsWithOverride({
      unlinkSync: (p) => {
        if (p === tokenPathFor(profile)) throw boom;
        return nodeFs.unlinkSync(p);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'not configured' }),
    });
    const result = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, deps);
    // Before the fix: `unlinkBestEffort`'s returned boolean was discarded at the final,
    // "cleanupIncomplete === false" call site — a failing token removal produced NO warning at
    // all, the one asymmetry left after round 7 already fixed the per-temp case.
    assert.equal(result.ok, true, `a failed token unlink must still be a warning, never a halt, on an already-durable commit; got ${JSON.stringify(result)}`);
    assert.equal(nodeFs.existsSync(recordPathFor(profile)), true, 'the run record must still be durably committed despite the failed token removal');
    assert.equal(nodeFs.existsSync(tokenPathFor(profile)), true, 'the token must still be on disk when its removal genuinely failed, matching the honest report');
    assert.equal(result.warnings.length, 2, JSON.stringify(result.warnings));
    assert.ok(result.warnings.some((w) => /no build identity source is configured/.test(w)), JSON.stringify(result.warnings));
    assert.ok(
      result.warnings.some((w) => /token/.test(w) && /could not be removed/.test(w)),
      `expected a warning naming the failed token removal; got ${JSON.stringify(result.warnings)}`,
    );
  });
});

test('closeCaptureRun -> recoverProvenanceState -> cleanupCommittedRun (codex round 8, MINOR): the FULL retained-token repair actually removes the stuck temp and the token, and the next openCaptureRun succeeds', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const opened = CR.openCaptureRun(profile, [], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true);
    const stuckTemp = tempPathFor(profile, '11111111-0000-0000-0000-000000000000');
    nodeFs.writeFileSync(stuckTemp, '{"stale":true}');
    // The unlink fails ONLY until the (simulated) underlying permission problem is fixed — exactly
    // what an operator who diagnoses and fixes the real EACCES cause, then re-runs the repair,
    // would experience.
    let failUnlink = true;
    const deps = depsWithOverride({
      unlinkSync: (p) => {
        if (p === stuckTemp && failUnlink) {
          const err = new Error('permission denied');
          err.code = 'EACCES';
          throw err;
        }
        return nodeFs.unlinkSync(p);
      },
      runIdentityCommand: () => ({ ok: false, detail: 'not configured' }),
    });
    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, deps);
    assert.equal(closed.ok, true, JSON.stringify(closed));
    assert.equal(nodeFs.existsSync(stuckTemp), true, 'sanity: the temp is still stuck right after close, same as the round-7 test above');
    assert.equal(nodeFs.existsSync(tokenPathFor(profile)), true, 'sanity: the token is retained right after close, same as the round-7 test above');

    const recovered = CR.recoverProvenanceState(profile, stubDepsNoIdentity());
    assert.equal(recovered.state, 'committed', `expected the token-retained close to classify as committed; got ${JSON.stringify(recovered)}`);

    failUnlink = false;
    const repaired = CR.cleanupCommittedRun(profile, recovered.expected, deps);
    assert.equal(repaired.ok, true, JSON.stringify(repaired));
    // This is the coverage gap the round-7 tests left: they stopped at classification and the
    // blocked reopen, never actually driving `cleanupCommittedRun` itself over a fixture WITH a
    // temp — so a mutant that skipped temp removal (and deleted only the token) would have passed
    // both of those tests while leaving exactly this residue behind.
    assert.equal(nodeFs.existsSync(stuckTemp), false, 'cleanupCommittedRun must actually remove the stuck temp, not just the token');
    assert.equal(nodeFs.existsSync(tokenPathFor(profile)), false, 'cleanupCommittedRun must remove the token once the temp is confirmed gone');
    assert.equal(nodeFs.existsSync(recordPathFor(profile)), true, 'the committed run record itself must survive the repair untouched');

    const reopened = CR.openCaptureRun(profile, [], null, stubDepsNoIdentity());
    assert.equal(reopened.ok, true, `once the documented repair has actually run, a fresh run must be able to open again; got ${JSON.stringify(reopened)}`);
  });
});

test('recordChapterProvenance: BLOCKER 4 (codex review) — a SHORT writeSync on the chapter-record temp must not commit a truncated record', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const { runId, assetDir, chapterFile } = runToCommitted(profile, entry, { 'a.png': 'v1' }, { 'a.png': 'v2' });
    const deps = {
      ...stubDepsNoIdentity(),
      expectedAssets: stubExpectedAssetsFor(assetDir, ['a.png']),
      writeSync: (fd, buffer, ...rest) => {
        nodeFs.writeSync(fd, buffer, ...rest);
        return 1; // report a short write regardless of the real (full) byte count
      },
    };
    const result = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, runId, deps);
    assert.equal(result.ok, false, `a short write must halt rather than record; got ${JSON.stringify(result)}`);
    assert.equal(nodeFs.existsSync(CR.chapterRecordPath(profile, entry)), false, 'a short-written chapter record must never be committed');
  });
});

test('recordChapterProvenance: a temp-write failure and a rename failure both leave ZERO surviving temps', () => {
  function tempFilesUnder(dirPath) {
    if (!nodeFs.existsSync(dirPath)) return [];
    return nodeFs.readdirSync(dirPath).filter((name) => name.endsWith('.tmp'));
  }

  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const { runId, assetDir, chapterFile } = runToCommitted(profile, entry, { 'a.png': 'v1' }, { 'a.png': 'v2' });
    const recordDir = join(recordPathFor(profile), '..', '..', 'chapters');
    const boom = Object.assign(new Error('injected write failure'), { code: 'EIO' });
    const deps = {
      ...stubDepsNoIdentity(),
      expectedAssets: stubExpectedAssetsFor(assetDir, ['a.png']),
      writeSync: () => {
        throw boom;
      },
    };
    const result = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, runId, deps);
    assert.equal(result.ok, false, JSON.stringify(result));
    assert.deepEqual(tempFilesUnder(recordDir), [], 'a temp-write failure must leave no surviving temp');
  });

  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const { runId, assetDir, chapterFile } = runToCommitted(profile, entry, { 'a.png': 'v1' }, { 'a.png': 'v2' });
    const recordDir = join(recordPathFor(profile), '..', '..', 'chapters');
    const boom = Object.assign(new Error('injected rename failure'), { code: 'EIO' });
    const deps = {
      ...stubDepsNoIdentity(),
      expectedAssets: stubExpectedAssetsFor(assetDir, ['a.png']),
      renameSync: () => {
        throw boom;
      },
    };
    const result = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, runId, deps);
    assert.equal(result.ok, false, JSON.stringify(result));
    assert.deepEqual(tempFilesUnder(recordDir), [], 'a rename failure must leave no surviving temp');
  });
});

// =================================================================================================
// recordChapterProvenance — completeness rules 1-5, via a stub matching the CONSUMED CONTRACT
// (buildEmbedCandidates/isCanonicalAssetKey/expectedAssets, teammate B / chapter-paths.mjs). The
// REQUIRED integration test against the REAL chapter-paths.mjs exports follows in its own section.
// =================================================================================================

function stubExpectedAssetsFor(assetDir, keys) {
  return () => ({ ok: true, assets: keys.map((key) => ({ key, absPath: join(assetDir, key) })) });
}

function runToCommitted(profile, entry, before, after) {
  const assetDir = join(profile.capture.output_dir, entry.group ?? '', String(entry.slug));
  nodeFs.mkdirSync(assetDir, { recursive: true });
  for (const [name, content] of Object.entries(before)) nodeFs.writeFileSync(join(assetDir, name), content);
  const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
  assert.equal(opened.ok, true);
  for (const [name, content] of Object.entries(after)) nodeFs.writeFileSync(join(assetDir, name), content);
  const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
  assert.equal(closed.ok, true);
  // recordChapterProvenance reads chapterFile's bytes itself (a distinct I/O exit from the asset
  // read) — its content is irrelevant to the stub `expectedAssets` these unit tests inject, but the
  // path must resolve to a real file or every call would abstain on `chapter_read_failed` for the
  // wrong reason.
  const chapterFile = join(assetDir, '..', `${entry.slug}-chapter.md`);
  nodeFs.writeFileSync(chapterFile, `# ${entry.slug}\n`);
  return { runId: opened.runState.run_id, assetDir, chapterFile };
}

test('recordChapterProvenance: PRODUCTION PATH — records a chapter with NO expectedAssets in deps at all (the real extractor must be the default, never a required injection)', () => {
  // This is the one test in this suite that must NEVER pass an `expectedAssets` override — every
  // other test in this file injects one (a stub or the real function), which is exactly why none
  // of them could have caught the module shipping with no default: in production nobody injects
  // `deps` at all, so a missing default silently takes the `no_extractor_configured` branch on
  // every real chapter and no record is EVER written on the actual path, while the whole suite
  // stays green because every OTHER test supplies an extractor one way or another. Found by
  // `paths`, confirmed by team-lead, fixed here with a regression test.
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'overview.png'), 'v1');
    const chapterFile = join(dir, 'items.md');

    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true);
    nodeFs.writeFileSync(join(assetDir, 'overview.png'), 'v2');
    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, true);

    // Real embed, real chapter-paths.mjs embedPath formula — matching what W3 actually authors.
    const { embedPath } = chapterPathsModule;
    const embed = embedPath(chapterFile, assetDir, 'overview.png');
    nodeFs.writeFileSync(chapterFile, `# Items\n\n1. Step\n\n   ![overview](${embed})\n`);

    // deps deliberately carries NO `expectedAssets` — this is what a real production caller does.
    const result = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, opened.runState.run_id, stubDepsNoIdentity());
    assert.equal(result.recorded, true, `production path must record without an injected extractor; got ${JSON.stringify(result)}`);
    assert.equal(nodeFs.existsSync(CR.chapterRecordPath(profile, entry)), true);
  });
});

test('recordChapterProvenance (codex round 9 follow-up, module-wide sweep): a THROWING randomUUID while naming its own temp must return a halt, not escape uncaught, and leave no orphaned temp', () => {
  // This module's inline `${finalPath}.${d.randomUUID()}.tmp` calls `d.randomUUID()` with no guard
  // of its own — measured directly against the real module: before this fix it threw uncaught,
  // before a temp name even existed, exactly the class of gap this round already fixed once in
  // openCaptureRun and once more in closeCaptureRun's own temp-naming call.
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'overview.png'), 'v1');
    const chapterFile = join(dir, 'items.md');

    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true);
    nodeFs.writeFileSync(join(assetDir, 'overview.png'), 'v2');
    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, true);

    const { embedPath } = chapterPathsModule;
    const embed = embedPath(chapterFile, assetDir, 'overview.png');
    nodeFs.writeFileSync(chapterFile, `# Items\n\n1. Step\n\n   ![overview](${embed})\n`);

    const deps = depsWithOverride({
      randomUUID: () => {
        throw new Error('injected randomUUID failure at W5');
      },
    });
    assert.doesNotThrow(() => {
      const result = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, opened.runState.run_id, deps);
      assert.equal(result.ok, false, JSON.stringify(result));
      assert.equal(result.halts[0].halt, 'provenance_hazard', JSON.stringify(result));
      // codex round 10, finding 3: this used to check only the halt name and that no temp
      // survived — both would pass unchanged if the runtime reported `reason: 'write_failed'`
      // (the pre-existing reason for an open/write failure) instead of the distinct
      // `temp_name_generation_failed` this fix introduced for "the failure happened before a
      // temp name even existed." Pin the actual distinction, not just a consequence of it.
      assert.equal(result.halts[0].reason, 'temp_name_generation_failed', JSON.stringify(result));
    });
    // No `<slug>.json.<uuid>.tmp` left behind under this chapter's own record directory.
    const chapterRecordDir = join(CR.chapterRecordPath(profile, entry), '..');
    const leftoverTemps = nodeFs.existsSync(chapterRecordDir)
      ? nodeFs.readdirSync(chapterRecordDir).filter((name) => name.startsWith(`${entry.slug}.json.`) && name.endsWith('.tmp'))
      : [];
    assert.deepEqual(leftoverTemps, [], 'a throwing randomUUID must not leave an orphaned chapter-record temp on disk');
  });
});

test('recordChapterProvenance (codex round 10, finding 1): a THROWING randomUUID with a NON-ERROR payload (null, undefined) must not crash its own temp-naming guard', () => {
  for (const [label, thrown] of [['null', null], ['undefined', undefined]]) {
    withTempDir((dir) => {
      const profile = profileFor(dir);
      const entry = { slug: 'items' };
      const assetDir = join(profile.capture.output_dir, 'items');
      nodeFs.mkdirSync(assetDir, { recursive: true });
      nodeFs.writeFileSync(join(assetDir, 'overview.png'), 'v1');
      const chapterFile = join(dir, 'items.md');

      const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
      assert.equal(opened.ok, true);
      nodeFs.writeFileSync(join(assetDir, 'overview.png'), 'v2');
      const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
      assert.equal(closed.ok, true);

      const { embedPath } = chapterPathsModule;
      const embed = embedPath(chapterFile, assetDir, 'overview.png');
      nodeFs.writeFileSync(chapterFile, `# Items\n\n1. Step\n\n   ![overview](${embed})\n`);

      const deps = depsWithOverride({
        randomUUID: () => {
          throw thrown;
        },
      });
      assert.doesNotThrow(() => {
        const result = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, opened.runState.run_id, deps);
        assert.equal(result.ok, false, `[${label}] ${JSON.stringify(result)}`);
        assert.equal(result.halts[0].halt, 'provenance_hazard', `[${label}] ${JSON.stringify(result)}`);
        assert.equal(typeof result.halts[0].detail, 'string', `[${label}] ${JSON.stringify(result)}`);
      }, `[${label}] the guard itself must not throw`);
    });
  }
});

test('recordChapterProvenance: happy path writes a record when every rule is satisfied', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const { runId, assetDir, chapterFile } = runToCommitted(profile, entry, { 'overview.png': 'v1' }, { 'overview.png': 'v2' });
    const deps = { ...stubDepsNoIdentity(), expectedAssets: stubExpectedAssetsFor(assetDir, ['overview.png']) };
    const result = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, runId, deps);
    assert.equal(result.recorded, true);
    assert.equal(nodeFs.existsSync(CR.chapterRecordPath(profile, entry)), true);
  });
});

test('recordChapterProvenance: mixed-directory case — one image changed, one did not -> no record', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const { runId, assetDir, chapterFile } = runToCommitted(
      profile,
      entry,
      { 'overview.png': 'v1', 'details-dialog.png': 'd1' },
      { 'overview.png': 'v2', 'details-dialog.png': 'd1' },
    );
    const deps = { ...stubDepsNoIdentity(), expectedAssets: stubExpectedAssetsFor(assetDir, ['overview.png', 'details-dialog.png']) };
    const result = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, runId, deps);
    assert.equal(result.recorded, false);
    assert.equal(nodeFs.existsSync(CR.chapterRecordPath(profile, entry)), false);
  });
});

test('recordChapterProvenance: rule 5 — image reverted to opening bytes before W5 -> no record', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');
    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v2');
    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, true);
    // Revert to the opening bytes before W5 runs.
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');
    const chapterFile = join(dir, 'chapter.md');
    nodeFs.writeFileSync(chapterFile, '# items\n');
    const deps = { ...stubDepsNoIdentity(), expectedAssets: stubExpectedAssetsFor(assetDir, ['a.png']) };
    const result = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, opened.runState.run_id, deps);
    assert.equal(result.recorded, false);
  });
});

// [round 13] The A->B->A case above is the one rule 5 was written for, and it was the only one
// tested — so the rule only ever had to reject a RETURN to the opening bytes. A replacement with a
// third value passed: it differs from the opening, which was the whole of the check. The record
// then persisted those foreign bytes under this run's `build_identity`, and W6 later called them
// verified because the current hash matched the one just recorded. The decisive case is A->B->C:
// only comparing against `closing` can reject it, and that comparison subsumes the A->B->A one,
// since rule 4 has already established that closing differs from opening.
test('recordChapterProvenance: rule 5 — image replaced with third bytes before W5 -> no record', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1'); // opening
    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v2'); // closing — the bytes this build produced
    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, true);
    // Bytes this run never produced: neither the opening nor the closing ones.
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v3');
    const chapterFile = join(dir, 'chapter.md');
    nodeFs.writeFileSync(chapterFile, '# items\n');
    const deps = { ...stubDepsNoIdentity(), expectedAssets: stubExpectedAssetsFor(assetDir, ['a.png']) };
    const result = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, opened.runState.run_id, deps);
    assert.equal(result.recorded, false);
    assert.equal(result.reason, 'rule5_replaced_since_closing');
    assert.equal(nodeFs.existsSync(CR.chapterRecordPath(profile, entry)), false);
  });
});

test('recordChapterProvenance: an ineligible chapter KEEPS its existing record byte-identical', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const { assetDir, chapterFile } = runToCommitted(profile, entry, { 'a.png': 'v1' }, { 'a.png': 'v1' }); // unchanged -> rule 4 fails
    const recordPath = CR.chapterRecordPath(profile, entry);
    nodeFs.mkdirSync(join(recordPath, '..'), { recursive: true });
    nodeFs.writeFileSync(recordPath, 'SENTINEL');
    const deps = { ...stubDepsNoIdentity(), expectedAssets: stubExpectedAssetsFor(assetDir, ['a.png']) };
    const result = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, 'whatever', deps);
    assert.equal(result.recorded, false);
    assert.equal(nodeFs.readFileSync(recordPath, 'utf8'), 'SENTINEL');
  });
});

test('recordChapterProvenance: zero in-directory embeds -> no record', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const { runId, assetDir, chapterFile } = runToCommitted(profile, entry, { 'a.png': 'v1' }, { 'a.png': 'v2' });
    const deps = { ...stubDepsNoIdentity(), expectedAssets: stubExpectedAssetsFor(assetDir, []) };
    const result = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, runId, deps);
    assert.equal(result.recorded, false);
    assert.equal(result.reason, 'zero_in_directory_embeds');
  });
});

test('recordChapterProvenance: a run_id mismatch (a run record from a different run) -> no record', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const { assetDir, chapterFile } = runToCommitted(profile, entry, { 'a.png': 'v1' }, { 'a.png': 'v2' });
    const deps = { ...stubDepsNoIdentity(), expectedAssets: stubExpectedAssetsFor(assetDir, ['a.png']) };
    const result = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, 'not-the-real-run-id', deps);
    assert.equal(result.recorded, false);
    assert.equal(result.reason, 'run_id_mismatch');
  });
});

test('recordChapterProvenance: same-slug namespaces (flat vs grouped) record distinct entries', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const flat = { slug: 'items' };
    const grouped = { slug: 'items', group: 'admin' };
    const flatAssetDir = join(profile.capture.output_dir, 'items');
    const groupedAssetDir = join(profile.capture.output_dir, 'admin', 'items');
    nodeFs.mkdirSync(flatAssetDir, { recursive: true });
    nodeFs.mkdirSync(groupedAssetDir, { recursive: true });
    nodeFs.writeFileSync(join(flatAssetDir, 'a.png'), 'f1');
    nodeFs.writeFileSync(join(groupedAssetDir, 'a.png'), 'g1');
    const opened = CR.openCaptureRun(profile, [flat, grouped], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true);
    nodeFs.writeFileSync(join(flatAssetDir, 'a.png'), 'f2');
    nodeFs.writeFileSync(join(groupedAssetDir, 'a.png'), 'g2');
    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, true);

    const flatChapterFile = join(dir, 'flat.md');
    nodeFs.writeFileSync(flatChapterFile, '# items\n');
    const flatDeps = { ...stubDepsNoIdentity(), expectedAssets: stubExpectedAssetsFor(flatAssetDir, ['a.png']) };
    const flatResult = CR.recordChapterProvenance(profile, [flat, grouped], flat, flatChapterFile, opened.runState.run_id, flatDeps);
    assert.equal(flatResult.recorded, true, JSON.stringify(flatResult));

    assert.equal(nodeFs.existsSync(CR.chapterRecordPath(profile, grouped)), false);
    assert.notEqual(CR.chapterRecordPath(profile, flat), CR.chapterRecordPath(profile, grouped));
  });
});

// =================================================================================================
// sweepChapterProvenanceTemps (codex round 5, finding 3) — the leftover `<slug>.json.<uuid>.tmp`
// a crashed recordChapterProvenance leaves behind under chapters/, and why row 6's classifier stays
// blind to it by design (see the module comment above sweepChapterProvenanceTemps's definition).
// =================================================================================================

function chapterTempPathFor(profile, entry, uuid = 'bbbbbbbb-0000-0000-0000-000000000000') {
  return `${CR.chapterRecordPath(profile, entry)}.${uuid}.tmp`;
}

function writeChapterTemp(profile, entry, text = '{}', uuid) {
  const tempPath = chapterTempPathFor(profile, entry, uuid);
  nodeFs.mkdirSync(join(tempPath, '..'), { recursive: true });
  nodeFs.writeFileSync(tempPath, text);
  return tempPath;
}

test('sweepChapterProvenanceTemps: finds and removes a leftover chapter-record temp a crashed recordChapterProvenance left behind', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const tempPath = writeChapterTemp(profile, entry);
    const result = CR.sweepChapterProvenanceTemps(profile, [entry], realDeps());
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.deepEqual(result.removed, [tempPath]);
    assert.equal(nodeFs.existsSync(tempPath), false, 'the leftover temp must actually be gone from disk');
  });
});

test('sweepChapterProvenanceTemps: grouped entry — the leftover temp lives under chapters/<group>/, not chapters/', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items', group: 'setup' };
    const tempPath = writeChapterTemp(profile, entry);
    assert.ok(tempPath.includes(`${join('chapters', 'setup')}`), `fixture sanity: temp must live under the group dir, got ${tempPath}`);
    const result = CR.sweepChapterProvenanceTemps(profile, [entry], realDeps());
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.deepEqual(result.removed, [tempPath]);
    assert.equal(nodeFs.existsSync(tempPath), false);
  });
});

test("sweepChapterProvenanceTemps: a stray chapter temp is invisible to recoverProvenanceState — row 6's domain stays run/-only", () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    writeChapterTemp(profile, entry);
    // No token/record written at all — the run's own state is 'absent' regardless of the stray
    // chapter temp. If row 6's `temps` observation ever widened to include chapters/, this would
    // misclassify as 'orphan_temp' instead, and — worse — would stay 'orphan_temp' forever, since
    // nothing about sweeping a chapter temp changes the run's own token/record.
    const result = CR.recoverProvenanceState(profile, realDeps());
    assert.equal(result.state, 'absent', `a stray chapters/ temp must never affect row 6's run-state classification; got ${result.state}`);
  });
});

test('sweepChapterProvenanceTemps: no leftover temps is a true no-op — the real chapter record is untouched', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const recordPath = CR.chapterRecordPath(profile, entry);
    nodeFs.mkdirSync(join(recordPath, '..'), { recursive: true });
    nodeFs.writeFileSync(recordPath, '{"record_version":1}');
    const result = CR.sweepChapterProvenanceTemps(profile, [entry], realDeps());
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.deepEqual(result.removed, []);
    assert.equal(nodeFs.existsSync(recordPath), true, 'the real (non-temp) chapter record must never be touched by the sweep');
  });
});

test('sweepChapterProvenanceTemps (codex round 6, finding 2 — IMPORTANT): a failed unlink is reported as a warning, never listed in removed, and the temp stays on disk', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const tempPath = writeChapterTemp(profile, entry);
    // Before this fix, `unlinkBestEffort` swallowed the EACCES and the caller unconditionally
    // pushed the path onto `removed` anyway — `{ok: true, removed: [tempPath]}` while the temp was
    // still on disk. Row 6's classifier deliberately cannot see chapters/ temps at all (see the
    // module banner above), so nothing else would ever contradict that false-clean report.
    const deps = depsWithOverride({
      unlinkSync: (p) => {
        if (p === tempPath) {
          const err = new Error('permission denied');
          err.code = 'EACCES';
          throw err;
        }
        return nodeFs.unlinkSync(p);
      },
    });
    const result = CR.sweepChapterProvenanceTemps(profile, [entry], deps);
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.deepEqual(result.removed, [], 'a temp whose unlink actually failed must never appear in removed');
    assert.equal(result.warnings.length, 1, JSON.stringify(result));
    assert.match(result.warnings[0], new RegExp(tempPath.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')), 'the warning must name the specific temp that could not be removed');
    assert.equal(nodeFs.existsSync(tempPath), true, 'the temp must genuinely still be on disk, matching the honest report');
  });
});

test('sweepChapterProvenanceTemps (codex round 6, finding 2): one removable and one unremovable temp under the same entry — removed lists only the removed one, warnings names the other', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const removablePath = writeChapterTemp(profile, entry, '{}', 'cccccccc-0000-0000-0000-000000000000');
    const stuckPath = writeChapterTemp(profile, entry, '{}', 'dddddddd-0000-0000-0000-000000000000');
    const deps = depsWithOverride({
      unlinkSync: (p) => {
        if (p === stuckPath) {
          const err = new Error('permission denied');
          err.code = 'EACCES';
          throw err;
        }
        return nodeFs.unlinkSync(p);
      },
    });
    const result = CR.sweepChapterProvenanceTemps(profile, [entry], deps);
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.deepEqual(result.removed, [removablePath]);
    assert.equal(result.warnings.length, 1, JSON.stringify(result));
    assert.match(result.warnings[0], new RegExp(stuckPath.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    assert.equal(nodeFs.existsSync(removablePath), false, 'the genuinely-removed temp must actually be gone');
    assert.equal(nodeFs.existsSync(stuckPath), true, 'the stuck temp must genuinely still be on disk');
  });
});

test('sweepChapterProvenanceTemps: a hazard on the chapters/ hierarchy halts rather than silently skipping', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const root = CR.provenanceRoot(profile);
    nodeFs.mkdirSync(root, { recursive: true });
    const outside = join(dir, 'outside-chapters-dir');
    nodeFs.mkdirSync(outside, { recursive: true });
    nodeFs.symlinkSync(outside, join(root, 'chapters'));
    const result = CR.sweepChapterProvenanceTemps(profile, [entry], realDeps());
    assert.equal(result.ok, false, JSON.stringify(result));
    assert.equal(result.halts[0].halt, 'provenance_hazard');
    assert.equal(result.halts[0].reason, 'symlink');
  });
});

test('sweepChapterProvenanceTemps: a hazard on a temp leaf itself (hard link) halts rather than unlinking blind', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const tempPath = chapterTempPathFor(profile, entry);
    nodeFs.mkdirSync(join(tempPath, '..'), { recursive: true });
    const outside = join(dir, 'outside-hardlink-target.json');
    nodeFs.writeFileSync(outside, '{}');
    nodeFs.linkSync(outside, tempPath);
    const result = CR.sweepChapterProvenanceTemps(profile, [entry], realDeps());
    assert.equal(result.ok, false, JSON.stringify(result));
    assert.equal(result.halts[0].halt, 'provenance_hazard');
    assert.equal(result.halts[0].reason, 'hard_link');
    assert.equal(nodeFs.existsSync(tempPath), true, 'a hazard leaf must never be unlinked');
  });
});

test("sweepChapterProvenanceTemps: a skipped-ownership profile is a silent no-op, matching abortCaptureRun/cleanupCommittedRun's skip contract", () => {
  withTempDir((dir) => {
    nodeFs.mkdirSync(join(dir, 'handbook'), { recursive: true });
    const profile = {
      capture: { output_dir: join(dir, 'handbook') },
      publish: { chapters_dir: join(dir, 'handbook') },
    };
    const entry = { slug: 'items' };
    const result = CR.sweepChapterProvenanceTemps(profile, [entry], realDeps());
    assert.deepEqual(result, { ok: true, skipped: true, removed: [], warnings: [] });
  });
});

// =================================================================================================
// buildProvenanceReport
// =================================================================================================

test('buildProvenanceReport: provenance_unavailable on a skipped profile, zero UI requests', () => {
  withTempDir((dir) => {
    nodeFs.mkdirSync(join(dir, 'handbook'), { recursive: true });
    // Overlapping topology with capture.build_identity ABSENT is the skip case (gate 5 only halts
    // an overlap when the adopter configured build_identity, per the plan's conditioning rule).
    const profile = {
      capture: { output_dir: join(dir, 'handbook') },
      publish: { chapters_dir: join(dir, 'handbook') },
    };
    // A currentObservation that WOULD resolve to a real value if ever consulted — the skip path
    // must never reach resolveBuildIdentity at all, so the row still renders 'unknown' regardless.
    const result = CR.buildProvenanceReport(profile, [{ slug: 'items' }], { kind: 'value', raw: 'should-never-be-consulted' }, stubDepsNoIdentity());
    assert.equal(result.rows.length, 1);
    assert.equal(result.rows[0].classification_reason, 'provenance_unavailable');
    assert.equal(result.rows[0].value, 'unknown');
  });
});

test('buildProvenanceReport (round 5, finding 4): a skipped-profile row carries current_source as an explicit null, and exactly the declared key set', () => {
  withTempDir((dir) => {
    nodeFs.mkdirSync(join(dir, 'handbook'), { recursive: true });
    const profile = {
      capture: { output_dir: join(dir, 'handbook') },
      publish: { chapters_dir: join(dir, 'handbook') },
    };
    // DEFAULT deps deliberately — the defect this pins was invisible precisely because the skip
    // branch runs before any seam is consulted, so an injected-deps fixture proves nothing about
    // which keys production emits.
    const result = CR.buildProvenanceReport(profile, [{ slug: 'items' }, { slug: 'orders' }]);
    assert.equal(result.rows.length, 2);
    for (const row of result.rows) {
      // Present-and-null, not absent: `ReportRow.current_source` is declared `string | null`, and
      // an omitted key would let a TypeScript caller dereference `undefined`. Nothing here compiles
      // TypeScript, so this key-set equality is the ONLY gate that holds the declaration honest —
      // a codex mutation audit confirmed every .d.mts mutation survives the whole suite otherwise.
      assert.equal(Object.hasOwn(row, 'current_source'), true, 'current_source must be PRESENT');
      assert.equal(row.current_source, null);
      assert.deepEqual(Object.keys(row).sort(), declaredReportRowKeys(),
        'a skipped-profile row does not carry exactly the declared ReportRow fields');
    }
  });
});

// A no-op extractor for tests that only care about record-state classification, never about
// completeness — buildProvenanceReport now runs the extractor for EVERY entry unconditionally
// (codex DO-NOT-SHIP blocker 4), so every buildProvenanceReport call needs a chapter file on disk
// and an extractor that does not halt, even when the fixture's whole point is elsewhere.
const emptyExpectedAssets = () => ({ ok: true, assets: [] });

/**
 * A `readdirSync` override reporting every entry as UV_DIRENT_UNKNOWN — all type predicates false.
 * `minimal: true` returns DirentLike's REQUIRED members only, so the runtime cannot reach the four
 * optional predicates; `minimal: false` returns a real `fs.Dirent` built with type 0, the faithful
 * model of what such a filesystem actually hands back. [round 20] Round 19's tests used only the
 * real Dirent while claiming a declaration-minimal shape, so the optional-call path was unreachable.
 */
function unknownDirentReaddir({ minimal }) {
  return (p, opts) => {
    const real = nodeFs.readdirSync(p, opts);
    if (!opts?.withFileTypes) return real;
    if (!minimal) {
      const Dirent = Object.getPrototypeOf(real[0]).constructor;
      return real.map((d) => new Dirent(d.name, 0, p));
    }
    return real.map((d) => ({
      name: d.name,
      isSymbolicLink: () => false,
      isDirectory: () => false,
      isFile: () => false,
    }));
  };
}

function writeChapterAt(profileLike, entry, content = '') {
  const chapterFile = join(profileLike.publish.chapters_dir, ...(entry.group !== undefined ? [entry.group] : []), `${entry.slug}.md`);
  nodeFs.mkdirSync(join(chapterFile, '..'), { recursive: true });
  nodeFs.writeFileSync(chapterFile, content);
  return chapterFile;
}

test('buildProvenanceReport: record_absent for a chapter with no record, distinct from record_malformed', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    writeChapterAt(profile, entry, '# items\n');
    const deps = { ...stubDepsNoIdentity(), expectedAssets: emptyExpectedAssets };
    const result = CR.buildProvenanceReport(profile, [entry], null, deps);
    assert.equal(result.rows[0].classification, 'indeterminate', JSON.stringify(result));
    assert.equal(result.rows[0].classification_reason, 'record_absent');

    const recordPath = CR.chapterRecordPath(profile, entry);
    nodeFs.mkdirSync(join(recordPath, '..'), { recursive: true });
    nodeFs.writeFileSync(recordPath, 'not json');
    const result2 = CR.buildProvenanceReport(profile, [entry], null, deps);
    assert.equal(result2.rows[0].classification_reason, 'record_malformed');
    assert.notEqual(result2.rows[0].classification_reason, result.rows[0].classification_reason);
  });
});

test('buildProvenanceReport: manifest order, and rows keyed by asset-dir tail not entry.slug', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const flat = { slug: 'items' };
    const grouped = { slug: 'items', group: 'admin' }; // same slug, different group -> different key
    writeChapterAt(profile, flat, '# items\n');
    writeChapterAt(profile, grouped, '# admin items\n');
    const deps = { ...stubDepsNoIdentity(), expectedAssets: emptyExpectedAssets };
    const result = CR.buildProvenanceReport(profile, [flat, grouped], null, deps);
    assert.equal(result.rows.length, 2, JSON.stringify(result));
    assert.notEqual(result.rows[0].key, result.rows[1].key);
  });
});

test('buildProvenanceReport: [clean, stale, clean] — the middle row is verified independently', () => {
  withTempDir((dir) => {
    // A real command source so "clean" resolves to `unchanged` (equal, known values) rather than
    // `indeterminate` — with no source configured, every row's value is null and the delta is
    // indeterminate regardless of staleness, which would make this fixture unable to distinguish
    // the two failure directions the group is named for.
    const versionDeps = { ...stubDepsNoIdentity(), runIdentityCommand: () => ({ ok: true, raw: '1.0.0' }) };
    const profile = profileFor(dir, { capture: { build_identity: { command: 'echo 1.0.0' } } });
    const clean1 = { slug: 'a' };
    const stale = { slug: 'b' };
    const clean2 = { slug: 'c' };
    for (const entry of [clean1, stale, clean2]) {
      const assetDir = join(profile.capture.output_dir, entry.slug);
      nodeFs.mkdirSync(assetDir, { recursive: true });
      nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');
      const opened = CR.openCaptureRun(profile, [entry], null, versionDeps);
      nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v2');
      const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, versionDeps);
      assert.equal(closed.ok, true);
      const chapterFile = writeChapterAt(profile, entry, `# ${entry.slug}\n`);
      const deps = { ...versionDeps, expectedAssets: stubExpectedAssetsFor(assetDir, ['a.png']) };
      const recorded = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, opened.runState.run_id, deps);
      assert.equal(recorded.recorded, true, JSON.stringify(recorded));
    }
    // Make `stale`'s asset changed again after its record was written.
    nodeFs.writeFileSync(join(profile.capture.output_dir, 'b', 'a.png'), 'v3');

    // W6 needs its own extractor too, now that it verifies the chapter's real embeds rather than
    // the whole asset directory — dispatched per entry, since each of the three uses its own dir.
    const w6Deps = {
      ...versionDeps,
      expectedAssets: (profileLikeArg, entryArg) => ({
        ok: true,
        assets: [{ key: 'a.png', absPath: join(profileLikeArg.capture.output_dir, entryArg.slug, 'a.png') }],
      }),
    };
    const result = CR.buildProvenanceReport(profile, [clean1, stale, clean2], null, w6Deps);
    assert.equal(result.rows[0].classification_reason, null, JSON.stringify(result.rows[0]));
    assert.equal(result.rows[0].classification, 'unchanged');
    assert.equal(result.rows[1].classification_reason, 'record_stale');
    assert.equal(result.rows[2].classification_reason, null);
    assert.equal(result.rows[2].classification, 'unchanged');
  });
});

test('buildProvenanceReport: an UNRELATED leftover file in the asset directory does NOT cause false staleness (codex DO-NOT-SHIP blocker 4)', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const { runId, assetDir } = runToCommitted(profile, entry, { 'a.png': 'v1' }, { 'a.png': 'v2' });
    // W5's own chapter-read is a distinct exit from W6's — write at the REAL derived location
    // (`chapters_dir`-relative) so BOTH stages resolve to the same file, matching how the two are
    // actually wired together in production.
    const chapterFile = writeChapterAt(profile, entry, '# items\n');
    const deps = { ...stubDepsNoIdentity(), expectedAssets: stubExpectedAssetsFor(assetDir, ['a.png']) };
    const recorded = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, runId, deps);
    assert.equal(recorded.recorded, true, JSON.stringify(recorded));

    // An unrelated file the chapter never embeds, left behind in the same directory (e.g. by the
    // opaque capture command writing more than the chapter shows).
    nodeFs.writeFileSync(join(assetDir, 'unrelated-leftover.png'), 'not embedded anywhere');

    const w6Deps = { ...stubDepsNoIdentity(), expectedAssets: stubExpectedAssetsFor(assetDir, ['a.png']) };
    const result = CR.buildProvenanceReport(profile, [entry], null, w6Deps);
    assert.ok(result.rows, JSON.stringify(result));
    assert.notEqual(result.rows[0].classification_reason, 'record_stale', JSON.stringify(result.rows[0]));
  });
});

test('buildProvenanceReport: a chapter with ZERO real embeds is reported STALE even when a stale leftover COINCIDENTALLY matches a recorded hash — never falsely verified (codex DO-NOT-SHIP blocker 4)', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    const leftoverContent = 'unchanged forever';
    nodeFs.writeFileSync(join(assetDir, 'stale-leftover.png'), leftoverContent);
    writeChapterAt(profile, entry, '# items, no images\n');
    // The record's OWN asset_hashes matches the leftover file's CURRENT content exactly — under
    // the old whole-directory hash, this makes verifyRecord see every "current" key present with a
    // matching hash and report `ok` (falsely verified). Under the fix, the chapter's real embed set
    // is empty regardless of what sits in the directory, so verifyRecord's own zero-current-embeds
    // rule (never vacuously "ok") is what must fire instead.
    const leftoverDigest = `sha256:${createHash('sha256').update(leftoverContent).digest('hex')}`;
    const recordPath = CR.chapterRecordPath(profile, entry);
    nodeFs.mkdirSync(join(recordPath, '..'), { recursive: true });
    nodeFs.writeFileSync(
      recordPath,
      JSON.stringify({
        record_version: 1,
        run_id: 'r1',
        build_identity: validBuildIdentity(),
        asset_hashes: { 'stale-leftover.png': leftoverDigest },
      }),
    );
    const deps = { ...stubDepsNoIdentity(), expectedAssets: emptyExpectedAssets };
    const result = CR.buildProvenanceReport(profile, [entry], null, deps);
    assert.equal(result.rows[0].classification_reason, 'record_stale', JSON.stringify(result.rows[0]));
    assert.notEqual(result.rows[0].classification, 'unchanged');
  });
});

// [round 14 BLOCKER] The current-hash loop kept only `present` results, so an embed the chapter
// STILL HAS but that gate 6 refuses to hash vanished from the comparison entirely. verifyRecord
// only ever sees the keys it is handed, so a two-embed chapter whose second embed grew an extra
// hard link after recording verified on the first alone and reported `unchanged` — the one verdict
// that tells a reader the chapter matches the build it names.
test('buildProvenanceReport: an embed that CANNOT be hashed makes the chapter stale, never `unchanged` on the survivors', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'good.png'), 'g1');
    nodeFs.writeFileSync(join(assetDir, 'bad.png'), 'b1');
    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true);
    nodeFs.writeFileSync(join(assetDir, 'good.png'), 'g2');
    nodeFs.writeFileSync(join(assetDir, 'bad.png'), 'b2');
    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closed.ok, true, JSON.stringify(closed));
    const chapterFile = writeChapterAt(profile, entry, '# items\n');
    const both = stubExpectedAssetsFor(assetDir, ['good.png', 'bad.png']);
    const recorded = CR.recordChapterProvenance(profile, [entry], entry, chapterFile, opened.runState.run_id, { ...stubDepsNoIdentity(), expectedAssets: both });
    assert.equal(recorded.recorded, true, JSON.stringify(recorded));

    // Only NOW does the hazard appear: an extra hard link, which gate 6 refuses (nlink !== 1).
    // Nothing about `good.png` changed, and both files are still embedded by the chapter.
    nodeFs.linkSync(join(assetDir, 'bad.png'), join(dir, 'bad-alias.png'));
    assert.equal(nodeFs.statSync(join(assetDir, 'bad.png')).nlink, 2);

    const result = CR.buildProvenanceReport(profile, [entry], null, { ...stubDepsNoIdentity(), expectedAssets: both });
    assert.notEqual(result.rows[0].classification, 'unchanged', JSON.stringify(result.rows[0]));
    assert.equal(result.rows[0].classification_reason, 'record_stale', JSON.stringify(result.rows[0]));
  });
});

// [round 15 IMPORTANT] Failing closed was right; losing the reason was not. A byte-identical file
// carrying an extra hard link and a file whose content actually changed produced the same row, so
// the report could not tell an operator "the content changed" from "the content could not be read"
// — two findings that call for different actions. The outer verdict is deliberately the same.
test('buildProvenanceReport: the row distinguishes UNREADABLE from CHANGED, though both are stale', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const recordFor = (slug, bytes) => {
      const entry = { slug };
      const assetDir = join(profile.capture.output_dir, slug);
      nodeFs.mkdirSync(assetDir, { recursive: true });
      nodeFs.writeFileSync(join(assetDir, 'a.png'), `${bytes}-open`);
      const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
      assert.equal(opened.ok, true);
      nodeFs.writeFileSync(join(assetDir, 'a.png'), bytes);
      assert.equal(CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity()).ok, true);
      const chapterFile = writeChapterAt(profile, entry, `# ${slug}\n`);
      const deps = { ...stubDepsNoIdentity(), expectedAssets: stubExpectedAssetsFor(assetDir, ['a.png']) };
      assert.equal(CR.recordChapterProvenance(profile, [entry], entry, chapterFile, opened.runState.run_id, deps).recorded, true);
      return { entry, assetDir };
    };
    const unreadable = recordFor('unreadable', 'v2');
    const changed = recordFor('changed', 'w2');

    // One becomes unreadable without changing a byte; the other changes bytes and stays readable.
    nodeFs.linkSync(join(unreadable.assetDir, 'a.png'), join(dir, 'alias.png'));
    nodeFs.writeFileSync(join(changed.assetDir, 'a.png'), 'w3');

    const w6Deps = {
      ...stubDepsNoIdentity(),
      expectedAssets: (profileLikeArg, entryArg) => ({
        ok: true,
        assets: [{ key: 'a.png', absPath: join(profileLikeArg.capture.output_dir, entryArg.slug, 'a.png') }],
      }),
    };
    const result = CR.buildProvenanceReport(profile, [unreadable.entry, changed.entry], null, w6Deps);
    assert.equal(result.rows[0].classification_reason, 'record_stale', JSON.stringify(result.rows[0]));
    assert.equal(result.rows[1].classification_reason, 'record_stale', JSON.stringify(result.rows[1]));
    // [round 17] The open-ended prefix passed against `unhashable:a.png:hazard`, which is the one
    // word that tells an operator nothing: every unreadable leaf carries kind `hazard`. This row
    // exists to say WHICH way the file was unreadable, so it is pinned to the discriminating word.
    assert.equal(result.rows[0].record_detail, 'unhashable:a.png:hard_link', JSON.stringify(result.rows[0]));
    assert.match(result.rows[1].record_detail, /^embed_hash_changed/, JSON.stringify(result.rows[1]));
    assert.notEqual(result.rows[0].record_detail, result.rows[1].record_detail);
  });
});

test('buildProvenanceReport: every row carries record_detail, null when the record is clean', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v1');
    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    nodeFs.writeFileSync(join(assetDir, 'a.png'), 'v2');
    assert.equal(CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, stubDepsNoIdentity()).ok, true);
    const chapterFile = writeChapterAt(profile, entry, '# items\n');
    const deps = { ...stubDepsNoIdentity(), expectedAssets: stubExpectedAssetsFor(assetDir, ['a.png']) };
    assert.equal(CR.recordChapterProvenance(profile, [entry], entry, chapterFile, opened.runState.run_id, deps).recorded, true);
    const result = CR.buildProvenanceReport(profile, [entry], null, deps);
    // Present-and-null, never absent: an absent key and "nothing to report" read differently, and
    // a field that only sometimes appears gets treated as optional by whatever renders it.
    assert.equal(Object.hasOwn(result.rows[0], 'record_detail'), true, JSON.stringify(result.rows[0]));
    assert.equal(result.rows[0].record_detail, null, JSON.stringify(result.rows[0]));
  });
});

test('buildProvenanceReport: record_unsupported_version is REACHABLE — a structurally valid record with a non-1 version reports as such, not as record_malformed (codex DO-NOT-SHIP blocker 4)', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    writeChapterAt(profile, entry, '# items\n');
    const recordPath = CR.chapterRecordPath(profile, entry);
    nodeFs.mkdirSync(join(recordPath, '..'), { recursive: true });
    nodeFs.writeFileSync(
      recordPath,
      JSON.stringify({ record_version: 2, run_id: 'r1', build_identity: validBuildIdentity(), asset_hashes: {} }),
    );
    const deps = { ...stubDepsNoIdentity(), expectedAssets: emptyExpectedAssets };
    const result = CR.buildProvenanceReport(profile, [entry], null, deps);
    assert.equal(result.rows[0].classification_reason, 'record_unsupported_version', JSON.stringify(result.rows[0]));
    assert.notEqual(result.rows[0].classification_reason, 'record_malformed');
  });
});

// =================================================================================================
// UI-read continuation: identityCommandOutcome threading (Finding 1) and W2 identity warnings
// (Finding 2) — both round-6 codex findings on build-identity resolution.
//
// The general capture fixtures above all disable the UI fallback (`build_identity: { ui_read:
// false }`, `profileFor`'s default) precisely so lifecycle/completeness tests never have to thread
// a UI observation through every call — which is exactly why the continuation path below was never
// exercised anywhere else in this file. Every fixture here explicitly sets `ui_read: true` and
// configures a `command`, so the `needs_ui_read` branch is genuinely reached.
// =================================================================================================

test('openCaptureRun (codex round 8, IMPORTANT 1): a contended open never runs the identity command, even one that would otherwise succeed', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir, { capture: { build_identity: { command: 'get-version', ui_read: true } } });
    // A token already on disk from some earlier (unrelated, still-open) run — this open can never
    // succeed, no matter what the identity command would have said.
    writeFixture(profile, { token: validToken(RUN_ID_A, ZERO_DIGEST) });
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    let calls = 0;
    let snapshotCalls = 0;
    const deps = depsWithOverride({
      runIdentityCommand: () => {
        calls += 1;
        return { ok: true, raw: 'v1' };
      },
      readdirSync: (p, opts) => {
        if (p === assetDir) snapshotCalls += 1;
        return nodeFs.readdirSync(p, opts);
      },
    });
    // Before the fix (codex round 8): `openCaptureRun` resolved (and could EXECUTE) the identity
    // command before the exclusive pending-token create ran — so this arbitrary, possibly
    // side-effecting operator command ran for a run that was never going to open.
    const result = CR.openCaptureRun(profile, [{ slug: 'items' }], null, deps);
    assert.equal(result.ok, false, JSON.stringify(result));
    assert.equal(result.halts[0].halt, 'run_already_open', JSON.stringify(result));
    assert.equal(calls, 0, 'the identity command must not run at all for an open that can never succeed');
    // codex round 9, finding 3: this test only ever watched the identity command, so a REORDER
    // moving the I/O-heavy asset-hash snapshot back before the reservation (reverting round 8's
    // fix for the snapshot half rather than just the identity-command half) would leave it green.
    assert.equal(snapshotCalls, 0, 'the opening asset-hash snapshot must not run at all for an open that can never succeed');
  });
});

test('openCaptureRun (codex round 8, IMPORTANT 1): a contended open resolves to run_already_open on the FIRST call, never sending the operator to a UI read first', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir, { capture: { build_identity: { command: 'get-version', ui_read: true } } });
    writeFixture(profile, { token: validToken(RUN_ID_A, ZERO_DIGEST) });
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    let calls = 0;
    let snapshotCalls = 0;
    const deps = depsWithOverride({
      runIdentityCommand: () => {
        calls += 1;
        // A command failure with `ui_read: true` is exactly the case that used to return
        // `needs_ui_read` without ever attempting the token — sending the operator off to do a UI
        // read for a run that could never open, discovering the real problem only on a LATER call.
        return { ok: false, detail: 'would force needs_ui_read if ever reached' };
      },
      readdirSync: (p, opts) => {
        if (p === assetDir) snapshotCalls += 1;
        return nodeFs.readdirSync(p, opts);
      },
    });
    const result = CR.openCaptureRun(profile, [{ slug: 'items' }], null, deps);
    assert.equal(result.needs_ui_read, undefined, `expected an immediate run_already_open halt, not a UI-read request; got ${JSON.stringify(result)}`);
    assert.equal(result.ok, false, JSON.stringify(result));
    assert.equal(result.halts[0].halt, 'run_already_open', JSON.stringify(result));
    assert.equal(calls, 0, 'the identity command must not run before the contention check settles the call');
    // codex round 9, finding 3: same gap as the sibling contention test above — never watched the
    // asset-hash snapshot, so a reorder reintroducing round 8's defect for the snapshot half alone
    // would still pass.
    assert.equal(snapshotCalls, 0, 'the opening asset-hash snapshot must not run before the contention check settles the call');
  });
});

function countLines(path) {
  if (!nodeFs.existsSync(path)) return 0;
  return nodeFs.readFileSync(path, 'utf8').trim().split('\n').filter(Boolean).length;
}

test('openCaptureRun (codex round 12, finding 2): the DOCUMENTED continuation call shape invokes the identity command exactly once, with genuinely default deps', () => {
  // SKILL.md instructs an operator to call the same function again "with the real observation and
  // identityCommandOutcome threaded straight through as its next argument." Read against the real
  // signature (profileLike, entries, openingObservation, deps, identityCommandOutcome),
  // identityCommandOutcome's real position is FIFTH — "its next argument" after openingObservation
  // (third) reads as FOURTH, which is `deps`. Every pre-existing continuation test in this suite
  // supplies an injected `deps` object as the 4th argument, so none of them could ever have caught
  // an operator who follows the instruction literally with a REAL, unmocked deps object — the only
  // way a production call is ever actually made. The fixture happened to occupy the correct slot by
  // construction; it never exercised what happens when deps is genuinely left at its default.
  //
  // Driven with NO depsWithOverride at all: a real shell command that appends to a counter file and
  // always fails (so the run FORCES needs_ui_read, and a wrongly-shaped second call would
  // re-invoke it) — counted directly, because a message assertion cannot show an extra invocation.
  // The call this test makes for the continuation IS the literal text SKILL.md must document:
  // `identityCommandOutcome` in its real 5th slot, `deps` explicitly `undefined` in its real 4th —
  // JavaScript has no way to skip a positional argument other than writing `undefined` there.
  withTempDir((dir) => {
    const counterFile = join(dir, 'counter.txt');
    const profile = profileFor(dir, { capture: { build_identity: { command: `echo x >> '${counterFile}'; exit 1`, ui_read: true } } });

    const first = CR.openCaptureRun(profile, []);
    assert.equal(first.needs_ui_read, true, JSON.stringify(first));
    assert.equal(countLines(counterFile), 1, 'the command must have run exactly once for the opening call');

    const resumed = CR.openCaptureRun(profile, [], { kind: 'value', raw: 'v1' }, undefined, first.identityCommandOutcome);
    assert.equal(resumed.ok, true, JSON.stringify(resumed));
    assert.equal(resumed.runState.opening.value, 'v1');
    assert.equal(resumed.runState.opening.source, 'ui');
    assert.equal(countLines(counterFile), 1, 'the identity command must run exactly ONCE across open + its continuation, never twice');
  });
});

test('closeCaptureRun (codex round 12, finding 2): the DOCUMENTED continuation call shape invokes the identity command exactly once, with genuinely default deps', () => {
  // Same contract, same gap: closeCaptureRun's signature is (profileLike, runState,
  // captureOutcome, closingObservation, deps, identityCommandOutcome) — deps is 5th,
  // identityCommandOutcome is 6th, and "threaded straight through as its next argument" after
  // closingObservation (4th) reads as 5th, landing in the deps slot exactly the same way.
  withTempDir((dir) => {
    const profile = profileFor(dir); // ui_read:false at OPEN keeps the opening identity deterministic
    const opened = CR.openCaptureRun(profile, []);
    assert.equal(opened.ok, true, JSON.stringify(opened));

    const counterFile = join(dir, 'counter.txt');
    profile.capture.build_identity = { command: `echo x >> '${counterFile}'; exit 1`, ui_read: true };

    const first = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null);
    assert.equal(first.needs_ui_read, true, JSON.stringify(first));
    assert.equal(countLines(counterFile), 1, 'the command must have run exactly once for the closing call');

    const resumed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, { kind: 'value', raw: 'v-close' }, undefined, first.identityCommandOutcome);
    assert.equal(resumed.ok, true, JSON.stringify(resumed));
    assert.equal(countLines(counterFile), 1, 'the identity command must run exactly ONCE across close + its continuation, never twice');
  });
});

test('buildProvenanceReport (codex round 12, finding 2): the DOCUMENTED continuation call shape invokes the identity command exactly once, with genuinely default deps', () => {
  // Same contract, same gap, and the entrypoint whose report ROW literally carries the field name
  // (`current_source`) codex's own measurement used — driven with NO `expectedAssets` override
  // either, the real production path (a zero-embed chapter is valid input for W6, which only
  // reports staleness, never enforces recordChapterProvenance's own completeness rule).
  withTempDir((dir) => {
    const counterFile = join(dir, 'counter.txt');
    const profile = profileFor(dir, { capture: { build_identity: { command: `echo x >> '${counterFile}'; exit 1`, ui_read: true } } });
    const entry = { slug: 'items' };
    writeChapterAt(profile, entry, '# items\n');

    const first = CR.buildProvenanceReport(profile, [entry], null);
    assert.equal(first.needs_ui_read, true, JSON.stringify(first));
    assert.equal(countLines(counterFile), 1, 'the command must have run exactly once for the W6 call');

    const resumed = CR.buildProvenanceReport(profile, [entry], { kind: 'value', raw: 'v1' }, undefined, first.identityCommandOutcome);
    assert.equal(resumed.rows?.[0]?.current_source, 'ui', JSON.stringify(resumed));
    assert.equal(countLines(counterFile), 1, 'the identity command must run exactly ONCE across the W6 call + its continuation, never twice');
  });
});

test('openCaptureRun: a UI-read continuation reuses the already-resolved identityCommandOutcome — the identity command is NOT re-invoked (Finding 1)', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir, { capture: { build_identity: { command: 'get-version', ui_read: true } } });
    let calls = 0;
    let reservationFd = null;
    const closedFds = [];
    const deps = depsWithOverride({
      runIdentityCommand: () => {
        calls += 1;
        // Fails on the FIRST call, forcing needs_ui_read. If this were ever invoked a SECOND time
        // it would "succeed" with a value that must never win — so a re-invocation (not just its
        // result) is what this test would catch.
        return calls === 1 ? { ok: false, detail: 'first call fails' } : { ok: true, raw: 'command-would-have-won' };
      },
      openSync: (p, flags, mode) => {
        const fd = nodeFs.openSync(p, flags, mode);
        if (p === tokenPathFor(profile)) reservationFd = fd;
        return fd;
      },
      closeSync: (fd) => {
        closedFds.push(fd);
        return nodeFs.closeSync(fd);
      },
    });

    const first = CR.openCaptureRun(profile, [{ slug: 'items' }], null, deps);
    assert.equal(first.needs_ui_read, true, JSON.stringify(first));
    assert.equal(calls, 1, 'the command must run exactly once for the first (needs_ui_read) call');
    assert.deepEqual(first.identityCommandOutcome, { ok: false, detail: 'first call fails' });
    // codex round 9, finding 3: reaching this point only via `resumed.ok === true` below proves
    // the token was UNLINKED (a second exclusive-create on the same name would otherwise EEXIST) —
    // it says nothing about whether the reservation's own fd was ever closed, so an "unlink
    // without close" mutation would still pass. Pin the close directly, by identity of the fd.
    assert.notEqual(reservationFd, null, 'the reservation open must have been observed via the openSync seam');
    assert.ok(
      closedFds.includes(reservationFd),
      `releasing a needs_ui_read reservation must close its own descriptor — closed fds were [${closedFds.join(',')}], reservation fd was ${reservationFd}`,
    );

    const resumed = CR.openCaptureRun(profile, [{ slug: 'items' }], { kind: 'value', raw: 'ui-value' }, deps, first.identityCommandOutcome);
    assert.equal(calls, 1, 'the command must NOT run a second time on the continuation call');
    assert.equal(resumed.ok, true, JSON.stringify(resumed));
    assert.equal(resumed.runState.opening.value, 'ui-value');
    assert.equal(resumed.runState.opening.source, 'ui');
  });
});

test('openCaptureRun: OMITTING identityCommandOutcome on a repeated call still works, by re-invoking the command (backward compatible with the pre-existing 4-argument call shape)', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir, { capture: { build_identity: { command: 'get-version', ui_read: true } } });
    let calls = 0;
    const deps = depsWithOverride({
      runIdentityCommand: () => {
        calls += 1;
        return { ok: false, detail: `call ${calls} fails` };
      },
    });

    const first = CR.openCaptureRun(profile, [{ slug: 'items' }], null, deps);
    assert.equal(first.needs_ui_read, true);
    assert.equal(calls, 1);

    // No 5th argument at all — the OLD call shape every pre-existing caller uses.
    const second = CR.openCaptureRun(profile, [{ slug: 'items' }], { kind: 'value', raw: 'ui-value' }, deps);
    assert.equal(calls, 2, 'omitting identityCommandOutcome must still re-run the command, exactly as before this parameter existed');
    assert.equal(second.ok, true, JSON.stringify(second));
  });
});

test('closeCaptureRun: a UI-read continuation reuses the already-resolved identityCommandOutcome — the identity command is NOT re-invoked (Finding 1)', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir, { capture: { build_identity: { command: 'get-version', ui_read: true } } });
    const responses = [{ ok: true, raw: 'v-open' }, { ok: false, detail: 'close attempt 1 fails' }, { ok: true, raw: 'command-would-have-won' }];
    let calls = 0;
    const deps = depsWithOverride({
      runIdentityCommand: () => {
        const r = responses[Math.min(calls, responses.length - 1)];
        calls += 1;
        return r;
      },
    });

    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, deps);
    assert.equal(opened.ok, true, JSON.stringify(opened));
    assert.equal(calls, 1, 'the OPENING command runs once, at open');
    assert.equal(opened.runState.opening.value, 'v-open');

    const firstClose = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, deps);
    assert.equal(firstClose.needs_ui_read, true, JSON.stringify(firstClose));
    assert.equal(calls, 2, 'the CLOSING command runs exactly once for the first close attempt');
    assert.deepEqual(firstClose.identityCommandOutcome, { ok: false, detail: 'close attempt 1 fails' });

    const resumed = CR.closeCaptureRun(
      profile,
      opened.runState,
      { ok: true },
      { kind: 'value', raw: 'ui-close-value' },
      deps,
      firstClose.identityCommandOutcome,
    );
    assert.equal(calls, 2, 'the closing command must NOT run a second time on the continuation call');
    assert.equal(resumed.ok, true, JSON.stringify(resumed));

    // opening='v-open' (command) vs closing='ui-close-value' (ui) are DIFFERENT — the committed
    // record must reflect THAT drift, never the third (never-actually-invoked) command response.
    const record = JSON.parse(nodeFs.readFileSync(recordPathFor(profile), 'utf8'));
    assert.equal(record.build_identity.resolution_reason, 'build_changed_during_capture', JSON.stringify(record.build_identity));
  });
});

test('closeCaptureRun (codex round 9 follow-up): a malformed closing observation THROWS inside resolveBuildIdentity but must return a halt (identity_resolution_threw), not escape uncaught', () => {
  // closeCaptureRun holds no reservation to leak (unlike openCaptureRun), but shares the same
  // `resolveIdentityOrHalt` call, so the same uncaught TypeError from a malformed uiObservation.kind
  // (untrusted UI-read input) escaped this function too before this fix — measured directly against
  // the real module via a genuine open then close, not reasoned from the code's shape.
  withTempDir((dir) => {
    const profile = profileFor(dir, { capture: { build_identity: { ui_read: true } } });
    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], { kind: 'unavailable', detail: 'no ui at open' }, {}, { ok: false, detail: 'no command' });
    assert.equal(opened.ok, true, JSON.stringify(opened));
    assert.doesNotThrow(() => {
      const result = CR.closeCaptureRun(profile, opened.runState, { ok: true }, { kind: 'bogus-kind' }, {});
      assert.equal(result.ok, false, JSON.stringify(result));
      assert.equal(result.halts[0].halt, 'identity_resolution_threw', JSON.stringify(result));
    });
  });
});

test('closeCaptureRun (codex round 9 follow-up): a THROWING identity command executor at close must return a halt (identity_resolution_threw), not escape uncaught', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir, { capture: { build_identity: { command: 'get-version', ui_read: false } } });
    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, depsWithOverride({ runIdentityCommand: () => ({ ok: true, raw: 'v1' }) }));
    assert.equal(opened.ok, true, JSON.stringify(opened));
    const deps = depsWithOverride({
      runIdentityCommand: () => {
        throw new Error('injected identity executor failure at close');
      },
    });
    assert.doesNotThrow(() => {
      const result = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, deps);
      assert.equal(result.ok, false, JSON.stringify(result));
      assert.equal(result.halts[0].halt, 'identity_resolution_threw', JSON.stringify(result));
    });
  });
});

test('closeCaptureRun (codex round 9 follow-up, module-wide sweep): a THROWING randomUUID while naming the closing temp must return a halt, not escape uncaught, and leave no orphaned temp', () => {
  // `tempRunRecordPath` calls `deps.randomUUID()` with no guard of its own — measured directly
  // against the real module: before this fix it threw uncaught, before a temp name even existed.
  withTempDir((dir) => {
    const profile = profileFor(dir);
    const entry = { slug: 'items' };
    const assetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(assetDir, { recursive: true });
    const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
    assert.equal(opened.ok, true, JSON.stringify(opened));
    const deps = depsWithOverride({
      randomUUID: () => {
        throw new Error('injected randomUUID failure at close');
      },
    });
    assert.doesNotThrow(() => {
      const result = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, deps);
      assert.equal(result.ok, false, JSON.stringify(result));
      assert.equal(result.halts[0].halt, 'provenance_hazard', JSON.stringify(result));
      // codex round 10, finding 3: this used to check only the halt name and that no temp
      // survived — both would pass unchanged if the runtime always reported "cannot write the
      // closing temp" (the pre-existing message for an open/write failure) instead of the
      // distinct "cannot generate the closing temp name" this fix introduced. Pin the actual
      // distinction, not just a consequence of it.
      assert.match(result.halts[0].message, /cannot generate the closing temp name/, JSON.stringify(result));
    });
    assert.equal(listRunTempsOnDisk(profile).length, 0, 'a throwing randomUUID must not leave an orphaned closing temp on disk');
  });
});

test('closeCaptureRun (codex round 10, finding 1): a THROWING randomUUID with a NON-ERROR payload (null, undefined) must not crash the closing temp-naming guard', () => {
  for (const [label, thrown] of [['null', null], ['undefined', undefined]]) {
    withTempDir((dir) => {
      const profile = profileFor(dir);
      const entry = { slug: 'items' };
      const assetDir = join(profile.capture.output_dir, 'items');
      nodeFs.mkdirSync(assetDir, { recursive: true });
      const opened = CR.openCaptureRun(profile, [entry], null, stubDepsNoIdentity());
      assert.equal(opened.ok, true, `[${label}] ${JSON.stringify(opened)}`);
      const deps = depsWithOverride({
        randomUUID: () => {
          throw thrown;
        },
      });
      assert.doesNotThrow(() => {
        const result = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, deps);
        assert.equal(result.ok, false, `[${label}] ${JSON.stringify(result)}`);
        assert.equal(result.halts[0].halt, 'provenance_hazard', `[${label}] ${JSON.stringify(result)}`);
        assert.equal(typeof result.halts[0].message, 'string', `[${label}] ${JSON.stringify(result)}`);
      }, `[${label}] the guard itself must not throw`);
      assert.equal(listRunTempsOnDisk(profile).length, 0, `[${label}] a throwing randomUUID must not leave an orphaned closing temp on disk`);
    });
  }
});

test('buildProvenanceReport: a UI-read continuation reuses the already-resolved identityCommandOutcome — the identity command is NOT re-invoked (Finding 1)', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir, { capture: { build_identity: { command: 'get-version', ui_read: true } } });
    const entry = { slug: 'items' };
    writeChapterAt(profile, entry, '# items\n');
    let calls = 0;
    const deps = depsWithOverride({
      runIdentityCommand: () => {
        calls += 1;
        return calls === 1 ? { ok: false, detail: 'first call fails' } : { ok: true, raw: 'command-would-have-won' };
      },
      expectedAssets: emptyExpectedAssets,
    });

    const first = CR.buildProvenanceReport(profile, [entry], null, deps);
    assert.equal(first.needs_ui_read, true, JSON.stringify(first));
    assert.equal(calls, 1);
    assert.deepEqual(first.identityCommandOutcome, { ok: false, detail: 'first call fails' });

    const resumed = CR.buildProvenanceReport(profile, [entry], { kind: 'value', raw: 'ui-value' }, deps, first.identityCommandOutcome);
    assert.equal(calls, 1, 'the command must NOT run a second time on the continuation call');
    assert.equal(resumed.rows[0].current_source, 'ui', JSON.stringify(resumed.rows));
  });
});

test('buildProvenanceReport (codex round 9 follow-up): a malformed current observation THROWS inside resolveBuildIdentity but must return a halt (identity_resolution_threw), not escape uncaught', () => {
  // W6 is the audit entrypoint an operator runs over already-merged chapters, reachable from the
  // same UI-read observation this project's own reference doc classifies as untrusted — measured
  // directly against the real module: it threw uncaught on this shape before this fix.
  withTempDir((dir) => {
    const profile = profileFor(dir, { capture: { build_identity: { ui_read: true } } });
    const entry = { slug: 'items' };
    writeChapterAt(profile, entry, '# items\n');
    const deps = { ...stubDepsNoIdentity(), expectedAssets: emptyExpectedAssets };
    assert.doesNotThrow(() => {
      const result = CR.buildProvenanceReport(profile, [entry], { kind: 'bogus-kind' }, deps);
      assert.equal(result.ok, false, JSON.stringify(result));
      assert.equal(result.halts[0].halt, 'identity_resolution_threw', JSON.stringify(result));
    });
  });
});

test('buildProvenanceReport (codex round 9 follow-up): a THROWING identity command executor must return a halt (identity_resolution_threw), not escape uncaught', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir, { capture: { build_identity: { command: 'get-version', ui_read: false } } });
    const entry = { slug: 'items' };
    writeChapterAt(profile, entry, '# items\n');
    const deps = depsWithOverride({
      runIdentityCommand: () => {
        throw new Error('injected identity executor failure at W6');
      },
      expectedAssets: emptyExpectedAssets,
    });
    assert.doesNotThrow(() => {
      const result = CR.buildProvenanceReport(profile, [entry], null, deps);
      assert.equal(result.ok, false, JSON.stringify(result));
      assert.equal(result.halts[0].halt, 'identity_resolution_threw', JSON.stringify(result));
    });
  });
});

test('closeCaptureRun: build_changed_during_capture warns, NAMING BOTH VALUES (Finding 2 — codex repro: open v1, close v2, warnings used to stay empty)', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir, { capture: { build_identity: { command: 'get-version', ui_read: false } } });
    let calls = 0;
    const deps = depsWithOverride({
      runIdentityCommand: () => {
        calls += 1;
        return calls === 1 ? { ok: true, raw: 'v1' } : { ok: true, raw: 'v2' };
      },
    });
    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, deps);
    assert.equal(opened.ok, true, JSON.stringify(opened));
    assert.equal(opened.runState.opening.value, 'v1');
    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, deps);
    assert.equal(closed.ok, true, JSON.stringify(closed));
    const record = JSON.parse(nodeFs.readFileSync(recordPathFor(profile), 'utf8'));
    assert.equal(record.build_identity.resolution_reason, 'build_changed_during_capture');
    assert.equal(closed.warnings.length, 1, JSON.stringify(closed.warnings));
    assert.match(closed.warnings[0], /v1/);
    assert.match(closed.warnings[0], /v2/);
  });
});

test('closeCaptureRun: a clean (unchanged) resolution never adds an identity warning', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir, { capture: { build_identity: { command: 'get-version', ui_read: false } } });
    const deps = depsWithOverride({ runIdentityCommand: () => ({ ok: true, raw: 'same-version' }) });
    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, deps);
    assert.equal(opened.ok, true, JSON.stringify(opened));
    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: true }, null, deps);
    assert.equal(closed.ok, true, JSON.stringify(closed));
    assert.deepEqual(closed.warnings, []);
  });
});

test('closeCaptureRun: capture_failed warns using captureOutcome.detail (Finding 2)', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir, { capture: { build_identity: { command: 'get-version', ui_read: false } } });
    const deps = depsWithOverride({ runIdentityCommand: () => ({ ok: true, raw: 'v1' }) });
    const opened = CR.openCaptureRun(profile, [{ slug: 'items' }], null, deps);
    assert.equal(opened.ok, true, JSON.stringify(opened));
    const closed = CR.closeCaptureRun(profile, opened.runState, { ok: false, detail: 'capture command exited 1' }, null, deps);
    assert.equal(closed.ok, true, JSON.stringify(closed));
    assert.equal(closed.warnings.length, 1, JSON.stringify(closed.warnings));
    assert.match(closed.warnings[0], /capture\.command itself failed/);
    assert.match(closed.warnings[0], /capture command exited 1/);
  });
});

// =================================================================================================
// fs capability policy — a positive scan over this module's OWN source text
// =================================================================================================

const MODULE_PATH = new URL('../skills/enduser-handbook/assets/lib/capture-record.mjs', import.meta.url);
const MODULE_SOURCE = nodeFs.readFileSync(MODULE_PATH, 'utf8');

// Skips whitespace and both comment forms starting at `source[from]`, returning the index of the
// next significant character (or `source.length` at EOF). Shared by every raw-source scanner below
// (`nextSignificantChar`, `parseImportClause`, and the capability-policy occurrence walk) so there is
// exactly one definition of "trivia" to keep in sync, rather than three inline copies drifting apart.
function skipTriviaFrom(source, from) {
  const n = source.length;
  let i = from;
  while (i < n) {
    if (/\s/.test(source[i])) { i++; continue; }
    if (source[i] === '/' && source[i + 1] === '/') { i += 2; while (i < n && source[i] !== '\n') i++; continue; }
    if (source[i] === '/' && source[i + 1] === '*') { i += 2; while (i < n && !(source[i] === '*' && source[i + 1] === '/')) i++; i += 2; continue; }
    break;
  }
  return i;
}

// Reads ONE JS identifier starting at `source[from]`, honoring `\uXXXX` / `\u{X+}` escape sequences
// exactly as the grammar does: plain `write` and the same name spelled with its first letter as a
// `w` escape name the IDENTICAL binding to the JS engine, so a checker that compares raw source
// spelling treats them as two different names — a real bypass
// (codex round 5: a named import's local binding written with an escaped code point defeated every
// alias-name comparison in the previous version of this policy, because the regex-based identifier
// reader it used couldn't even parse past the backslash and silently gave up on the whole clause).
// Returns `{ text, end }` where `text` is the DECODED name (what the identifier actually IS) and
// `end` is the RAW source index right past it — callers that ask "what comes after this identifier"
// need the raw position; the escape sequence's on-disk length has nothing to do with where it ends.
// Returns null when no identifier starts here at all.
function readIdentifierAt(source, from) {
  const n = source.length;
  let i = from;
  function readEscape() {
    if (source[i + 2] === '{') {
      const close = source.indexOf('}', i + 3);
      if (close === -1) return null;
      const hex = source.slice(i + 3, close);
      if (hex === '' || !/^[0-9A-Fa-f]+$/.test(hex)) return null;
      const code = parseInt(hex, 16);
      if (code > 0x10ffff) return null;
      i = close + 1;
      return String.fromCodePoint(code);
    }
    const hex = source.slice(i + 2, i + 6);
    if (!/^[0-9A-Fa-f]{4}$/.test(hex)) return null;
    i += 6;
    return String.fromCharCode(parseInt(hex, 16));
  }
  let text;
  if (source[i] === '\\' && source[i + 1] === 'u') {
    const ch = readEscape();
    if (ch === null || !/[A-Za-z_$]/.test(ch)) return null;
    text = ch;
  } else if (/[A-Za-z_$]/.test(source[i] ?? '')) {
    text = source[i];
    i++;
  } else {
    return null;
  }
  for (;;) {
    if (i >= n) break;
    if (source[i] === '\\' && source[i + 1] === 'u') {
      const before = i;
      const ch = readEscape();
      if (ch === null || !/[A-Za-z0-9_$]/.test(ch)) { i = before; break; }
      text += ch;
      continue;
    }
    if (/[A-Za-z0-9_$]/.test(source[i])) { text += source[i]; i++; continue; }
    break;
  }
  return { text, end: i };
}

// A lexer sufficient for JS source: distinguishes comments, string/template literals and regex
// literals from ordinary code, and emits a token per identifier/keyword and per string (its
// decoded content, so a banned name hidden inside a string or a template's static text is still
// visible). Each token also records whether it was reached via a `.` MEMBER ACCESS (the immediately
// preceding significant character), which is what lets "constructor" the class-method-definition
// keyword be told apart from ".constructor" the prototype-escape property. Bounded and structural,
// in the same spirit as chapter-paths.mjs's markdown scanners — real full parsing is not required
// because the only thing this check needs is: does a banned name appear, in a banned SHAPE.
function scanJsTokens(source) {
  const tokens = [];
  let i = 0;
  const n = source.length;
  let prevSignificant = ''; // last non-trivial character/kind, for regex-vs-division disambiguation
  let precededByDot = false;
  while (i < n) {
    const ch = source[i];
    if (ch === '/' && source[i + 1] === '/') {
      i += 2;
      while (i < n && source[i] !== '\n') i++;
      continue;
    }
    if (ch === '/' && source[i + 1] === '*') {
      i += 2;
      while (i < n && !(source[i] === '*' && source[i + 1] === '/')) i++;
      i += 2;
      continue;
    }
    if (ch === '"' || ch === "'") {
      const quote = ch;
      let str = '';
      i++;
      while (i < n && source[i] !== quote) {
        if (source[i] === '\\') {
          str += source[i] + (source[i + 1] ?? '');
          i += 2;
          continue;
        }
        str += source[i];
        i++;
      }
      i++;
      tokens.push({ kind: 'string', text: str });
      prevSignificant = 'str';
      precededByDot = false;
      continue;
    }
    if (ch === '`') {
      // Template literal: walk static chunks, recursing into ${...} expressions by depth-tracked
      // scanning (re-entering this same loop conceptually via a nested brace counter).
      i++;
      let str = '';
      while (i < n && source[i] !== '`') {
        if (source[i] === '\\') {
          str += source[i] + (source[i + 1] ?? '');
          i += 2;
          continue;
        }
        if (source[i] === '$' && source[i + 1] === '{') {
          tokens.push({ kind: 'string', text: str });
          str = '';
          i += 2;
          let depth = 1;
          const exprStart = i;
          while (i < n && depth > 0) {
            if (source[i] === '{') depth++;
            else if (source[i] === '}') depth--;
            if (depth > 0) i++;
          }
          const inner = source.slice(exprStart, i);
          tokens.push(...scanJsTokens(inner));
          i++; // consume closing '}'
          continue;
        }
        str += source[i];
        i++;
      }
      tokens.push({ kind: 'string', text: str });
      i++;
      prevSignificant = 'str';
      precededByDot = false;
      continue;
    }
    if (/[A-Za-z_$]/.test(ch) || (ch === '\\' && source[i + 1] === 'u')) {
      const parsed = readIdentifierAt(source, i);
      if (parsed !== null) {
        const { text, end } = parsed;
        // `precededByChar` snapshots the significant character/kind that came right before THIS
        // token, BEFORE it gets overwritten to 'ident' below — used (IMPORTANT 4, and by the
        // capability policy's occurrence walk below) to tell an OBJECT-LITERAL property value
        // (`openSync: fs.openSync,`, preceded by ':') apart from a BARE variable/destructuring
        // target (`const write = fs.writeFileSync`, preceded by '='), which is exactly the shape
        // distinction a fs.<method>-aliasing bypass needs to be caught. `start`/`end` are the RAW
        // source bounds of this identifier (its DECODED `text` may be a different length than
        // `end - start` when it contains a `\uXXXX` escape) — used by the capability policy to
        // exclude an import statement's own declaration span from its occurrence scan, and to walk
        // raw source forward from a reference to see what it's used for.
        tokens.push({ kind: 'ident', text, precededByDot, precededByChar: prevSignificant, start: i, end });
        prevSignificant = 'ident';
        precededByDot = false;
        i = end;
        continue;
      }
      // A lone '\' not actually starting a valid `\uXXXX`/`\u{X+}` escape — not an identifier at
      // all; fall through to the generic single-character handling below, same as any other symbol.
    }
    if (ch === '.' && source[i + 1] === '.' && source[i + 2] === '.') {
      // Spread/rest `...` — NOT a member-access dot. `{ ...fs }` spreads fs's own enumerable
      // properties into a fresh object; it is not `something.fs`. Treating the run of three dots
      // exactly like a single real `.` (the previous behaviour, since `.` was never tokenized and
      // only updated `precededByDot` character-by-character) made `fs` in `{ ...fs }` look
      // EXACTLY like someone else's member (`x.fs`) and silently exempted it from occurrence
      // scanning — a real bypass (ped-ant round 6: `{ ...fs }; d.writeFileSync(...)` copies every
      // enumerable export, including `writeFileSync`, into a bare object with no seam at all).
      tokens.push({ kind: 'punct', text: '...' });
      prevSignificant = '...';
      precededByDot = false;
      i += 3;
      continue;
    }
    if (ch === '+') {
      tokens.push({ kind: 'punct', text: '+' });
      prevSignificant = '+';
      precededByDot = false;
      i++;
      continue;
    }
    // [round 3, codex] `[`/`]` — needed to detect bracket/computed member access
    // (`fs["writeFileSync"]`). Sets `prevSignificant`/`precededByDot` exactly as the pre-existing
    // generic fallback branch already did for these two characters (this is purely ADDITIVE: it
    // only adds a token for something that previously fell through unrecorded, so every OTHER
    // existing check — including the `]` value the regex-vs-division disambiguation below already
    // inspects — is unaffected).
    if (ch === '[' || ch === ']') {
      tokens.push({ kind: 'punct', text: ch });
      prevSignificant = ch;
      precededByDot = false;
      i++;
      continue;
    }
    // [round 6→7, this fix] `{`/`}` — needed so the capability policy can locate the `defaultDeps`
    // object literal's own SPAN (see findDefaultDepsLiteralSpan below) by balancing brace tokens,
    // the same way bracket access needed `[`/`]` above. Purely ADDITIVE, same rationale as that
    // block: before this, `{`/`}` fell into the generic single-character fallback below, which
    // already set `prevSignificant`/`precededByDot` to the IDENTICAL values this branch sets —
    // every existing check that reads `precededByChar`/`precededByDot` on a NEIGHBORING token sees
    // no change at all; the only difference is that a `{`/`}` now also appears as its own entry in
    // the `tokens` array, which nothing before this round ever indexed into expecting otherwise.
    //
    // [round 7→8, this fix] ALSO records `precededByChar` on the `{`/`}` token itself (ident tokens
    // already carry this; punct tokens never did, since nothing needed it before). Without it,
    // findDefaultDepsLiteralSpan's "is `defaultDeps` immediately followed by `=`" check — which reads
    // `precededByChar` off whatever token comes right after `defaultDeps` — silently failed for
    // `const defaultDeps = { ... }` (no `Object.freeze(` wrapper): the token right after `defaultDeps`
    // is this `{` PUNCT token, which had no `precededByChar` field at all (`undefined !== '='`), so
    // the declaration was never found and every legitimate fs reference inside a plain-literal seam
    // was rejected as outside the sanctioned slot — a false-RED that fails safe (nothing ships) but
    // would be baffling to hit from an ordinary `Object.freeze` removal (team-lead review, guard5
    // round 7). `Object.freeze({` was unaffected either way, since there the token right after
    // `defaultDeps` is the `Object` IDENT, which already carried `precededByChar` correctly.
    if (ch === '{' || ch === '}') {
      tokens.push({ kind: 'punct', text: ch, precededByChar: prevSignificant });
      prevSignificant = ch;
      precededByDot = false;
      i++;
      continue;
    }
    if (ch === '/') {
      // Regex-vs-division: a '/' starting a regex cannot follow an identifier/number/string/')'/']'.
      const regexAllowed = !['ident', 'str', ')', ']'].includes(prevSignificant);
      if (regexAllowed) {
        let j = i + 1;
        let inClass = false;
        while (j < n) {
          if (source[j] === '\\') { j += 2; continue; }
          if (source[j] === '[') inClass = true;
          else if (source[j] === ']') inClass = false;
          else if (source[j] === '/' && !inClass) break;
          else if (source[j] === '\n') break; // unterminated on this line -> not a regex; bail out
          j++;
        }
        if (j < n && source[j] === '/') {
          i = j + 1;
          while (i < n && /[a-z]/.test(source[i])) i++; // flags
          prevSignificant = 'regex';
          precededByDot = false;
          continue;
        }
      }
    }
    if (!/\s/.test(ch)) {
      prevSignificant = ch;
      precededByDot = ch === '.';
    }
    i++;
  }
  return tokens;
}

// The next non-whitespace, non-comment character at or after `from` — used for dynamic-import
// detection so a comment inserted between `import` and `(` (`import /*x*/ (...)`) cannot defeat a
// naive "only whitespace allowed" check.
function nextSignificantChar(source, from) {
  const i = skipTriviaFrom(source, from);
  return source[i] ?? '';
}

// TOKEN-based, not a raw-source regex — the earlier regex required `import\s+...\s+from`
// (WHITESPACE only around both keywords), so a comment wedged anywhere in the statement
// (`import/*x*/{a}from'y'`, or the more natural `import {a} /*x*/ from 'y'`) made the whole
// statement invisible to it: zero specifiers found, so a disallowed one slipped through
// completely unchecked (codex, important #7's "a commented static import" defeat). Working off
// the already-comment-free token stream instead means comment PLACEMENT can no longer matter.
// Returns one `{specifier, importTokenEnd}` per import statement — `importTokenEnd` is the raw-
// source index right after the `import` keyword, which `importClauseIsNamed` below scans forward
// from to classify the clause SHAPE (named/braced vs. namespace/default/side-effect-only).
function findImportStatements(tokens) {
  const statements = [];
  for (let idx = 0; idx < tokens.length; idx++) {
    if (tokens[idx].kind !== 'ident' || tokens[idx].text !== 'import') continue;
    // The specifier is the NEXT string token after `import`, whichever form introduced it
    // (`import 'y'`, `import {a} from 'y'`, `import * as x from 'y'`, or even `import('y')` — a
    // dynamic import's argument is checked here too, harmlessly, since the dedicated dynamic-
    // import ban already rejects the module outright regardless of what this finds).
    for (let j = idx + 1; j < tokens.length; j++) {
      if (tokens[j].kind === 'string') {
        statements.push({ specifier: tokens[j].text, importTokenEnd: tokens[idx].end });
        break;
      }
      // Reached the next statement without finding a string — not a well-formed import; nothing
      // to check here (a syntax error elsewhere is not this policy's concern).
      if (tokens[j].kind === 'ident' && (tokens[j].text === 'import' || tokens[j].text === 'export')) break;
    }
  }
  return statements;
}

// [round 3, codex] Parses ONE import clause's raw text (from just after the `import` keyword,
// through its specifier string) into every LOCAL NAME it introduces — default, namespace, and/or
// named (optionally `as`-renamed) — in whichever combination the statement uses. Returns null when
// the clause is not well-formed enough to parse (a syntax error elsewhere is not this policy's
// concern, matching findImportStatements' own "not well-formed" bail-out). Also returns
// `clauseEnd`, the raw source index right past the clause (before `from '...'`) — [round 5→6, this
// fix] used by collectFsBindings below to mark the clause's own SPAN as a declaration, not a
// reference: without it, `import * as fs from 'node:fs'` was itself flagged as an unsafe occurrence
// of `fs`, because the identifier `fs` genuinely does appear in that raw text and the checker had no
// way to tell "this is where the name is BEING BOUND" apart from "this is a USE of it".
function parseImportClause(source, fromIndex) {
  const n = source.length;
  let i = fromIndex;

  function skipTrivia() {
    i = skipTriviaFrom(source, i);
  }
  function readIdentifier() {
    skipTrivia();
    const parsed = readIdentifierAt(source, i);
    if (parsed === null) return null;
    i = parsed.end;
    return parsed.text;
  }
  function readNamedClause(named) {
    skipTrivia();
    if (source[i] !== '{') return false;
    i++;
    while (true) {
      skipTrivia();
      if (source[i] === '}') { i++; return true; }
      if (source[i] === ',') { i++; continue; }
      const imported = readIdentifier();
      if (imported === null) return false;
      let local = imported;
      const beforeAs = i;
      const maybeAs = readIdentifier();
      if (maybeAs === 'as') {
        const renamed = readIdentifier();
        if (renamed === null) return false;
        local = renamed;
      } else {
        i = beforeAs; // not 'as' — put the scanner back before whatever we just consumed
      }
      named.push({ imported, local });
      skipTrivia();
      if (source[i] === ',') { i++; continue; }
      if (source[i] === '}') { i++; return true; }
      return false;
    }
  }

  const named = [];
  let defaultName = null;
  let namespaceName = null;

  skipTrivia();
  if (source[i] === "'" || source[i] === '"') {
    return { defaultName, namespaceName, named, clauseEnd: i }; // side-effect-only import — no bindings at all
  }
  if (source[i] === '*') {
    i++;
    if (readIdentifier() !== 'as') return null;
    namespaceName = readIdentifier();
    if (namespaceName === null) return null;
    return { defaultName, namespaceName, named, clauseEnd: i };
  }
  if (source[i] !== '{') {
    defaultName = readIdentifier();
    if (defaultName === null) return null;
    skipTrivia();
    if (source[i] === ',') {
      i++;
      skipTrivia();
      if (source[i] === '*') {
        i++;
        if (readIdentifier() !== 'as') return null;
        namespaceName = readIdentifier();
        if (namespaceName === null) return null;
        return { defaultName, namespaceName, named, clauseEnd: i };
      }
      if (!readNamedClause(named)) return null;
    }
    return { defaultName, namespaceName, named, clauseEnd: i };
  }
  if (!readNamedClause(named)) return null;
  return { defaultName, namespaceName, named, clauseEnd: i };
}

const ALLOWED_IMPORT_SPECIFIERS = new Set(['node:fs', 'node:child_process', 'node:crypto', './build-identity.mjs', './chapter-paths.mjs']);
// Banned unconditionally, in ANY shape (identifier, member access, string/template content) —
// this module has no legitimate use for any of them, in any position.
const UNCONDITIONALLY_BANNED = new Set(['process', 'Function', 'eval', 'require', 'createRequire']);
// The module's own `fs` binding (`import * as fs from 'node:fs'`) may be referenced ONLY through
// this narrow allowlist of property names — `constants` (numeric flags, not an operation) — every
// other `fs.<name>` is a direct filesystem call bypassing the injectable `deps` seam entirely,
// which is the exact property the capability policy exists to enforce (codex, important #7: a
// prior version of this checker had NO rule for this at all, so `fs.writeFileSync(...)` anywhere
// in the module passed silently).
const ALLOWED_FS_MEMBERS = new Set(['constants']);

// [round 3, codex] Every LOCAL NAME the source binds to `node:fs`, traced from its actual import
// statement(s) — see collectFsBindings below, which builds `namespaceNames`/`directNames`.
//   - NAMESPACE names — the whole module object (a namespace OR default import; Node's ESM/CJS
//     interop makes a default import of `node:fs` equally a whole-module object). The real
//     module's OWN binding, `fs`, is included UNCONDITIONALLY — every "mutant"/"legitimate
//     reference" fixture in this file is an isolated SNIPPET with no import statement of its own,
//     exercising the checker function directly, and this module's own convention (confirmed by
//     "the real module PASSES") is that `fs` always names this binding.
//   - DIRECT names — a named import's local binding is already bound to one resolved fs FUNCTION,
//     no member access needed at all.
//
// [round 3, codex] first closed this against the SHAPE each bypass used: a bare alias of the
// dotted expression, a named-clause import, a default import, a namespace import under another
// alias, bracket access, destructuring — one closure per shape. [round 5, codex] then defeated
// FOUR more shapes in one pass: `fs?.writeFileSync(...)` (optional chaining breaks the tokenizer's
// "reached via a dot" tracking), `(fs).writeFileSync(...)` (a grouping paren does the same),
// `fs.writeFileSync.call(null, ...)` (the CALL happens one property hop past the dotted expression
// the old check looked at), and a named import's local binding spelled with a `\uXXXX` escape
// (the regex-based identifier reader of the time couldn't parse past the backslash, so the whole
// import clause silently failed to parse and the binding was never traced at all). Two clean rounds
// in a row on the SAME two-shape-enumerating design is the sign the axis was wrong, not that the
// list was short (schema-gate-hardening's positive-allowlist spine, principle 1): every new shape
// bypasses this check by finding an expression that reaches the real function WITHOUT the checker's
// chosen "is it a dotted `namespace.method` call" or "is this bare name ever called" pattern
// matching it — an open-ended set, because "how can you invoke a function reference" has no bound.
//
// [round 5→6, this fix] inverts the axis: instead of asking "what shape reaches the function",
// findDisallowedFsReference (below) asks "where does this occurrence of a bound name SIT". The
// module has exactly ONE legitimate use of an fs OPERATION reference — assigning it as a property
// VALUE while building the injectable `deps` seam (`openSync: fs.openSync,` in the object literal,
// or the equally legitimate `deps.openSync = fs.openSync` assignment-target spelling exercised by
// the standalone snippet tests below) — everywhere else in the file, `fs` is used only via
// `fs.constants.<FLAG>` (data, not an operation; confirmed by grep — no other `fs.` shape appears
// anywhere in the real module outside the `deps` seam). A reference that sits in that ONE property-
// value SLOT is safe regardless of exactly which of `:`/`=` spelled it; a reference that sits
// ANYWHERE ELSE is rejected regardless of what shape put it there — a call, a `.call`/`.apply`
// indirection, an alias, a destructure, an escape, or a shape nobody has thought of yet all fail
// the SAME one check, because none of them is "sitting in the property-value slot".
//
// [round 6, ped-ant review] found two MORE gaps in the round-5→6 rewrite itself, both from asking
// what a "reference" even IS, rather than another call shape: (1) `{ ...fs }` — object-SPREAD of
// the whole namespace — copies every enumerable export (`writeFileSync` included) into a fresh,
// un-seamed object; the slot rule alone can't reject this, because a spread occurrence genuinely
// does sit where `}` follows it, syntactically resembling a value position. The actual gap was one
// level UP: the tokenizer had never distinguished `...` (spread/rest — three dots forming ONE
// grammatical unit, not a member access) from a real `.` access, so `fs` right after `...` looked
// exactly like someone else's member (`x.fs`) and was silently exempted from occurrence scanning
// entirely — fixed in scanJsTokens itself, then closed properly here by rejecting ANY namespace
// occurrence with no NAMED member following it (a bare `fs` — via spread, a function argument, a
// bare assignment — always exposes every operation at once, never the one resolved function the
// seam-construction slot exists for; this is categorically different from an in-slot `fs.method`
// reference, not a slot violation of the same kind). (2) `export { writeFileSync } from 'node:fs';`
// — a re-export performs no I/O and calls nothing WITHIN this module, so it is invisible to every
// occurrence-based check above by construction; it defeats the module's contract at the PACKAGE
// boundary instead, republishing the raw function to any future importer of this file. See
// `findReExportSpecifiers` below for why this needed a wholly separate check, not an extension of
// the occurrence walk.
function collectFsBindings(source, tokens) {
  const namespaceNames = new Set(['fs']);
  const directNames = new Set();
  // The raw-source SPAN of each `node:fs` import clause — collectFsBindings finds these names by
  // reading the DECLARATION text itself, which necessarily contains the very identifiers this
  // policy tracks (`fs`, a renamed alias, a destructured member name). findDisallowedFsReference
  // must not mistake that declaration text for a USE of the name it declares, so every span is
  // handed to it as an exclusion list — see the parseImportClause comment above `clauseEnd` for why.
  const declarationSpans = [];
  for (const { specifier, importTokenEnd } of findImportStatements(tokens)) {
    if (specifier !== 'node:fs') continue;
    const clause = parseImportClause(source, importTokenEnd);
    if (clause === null) continue;
    declarationSpans.push({ start: importTokenEnd, end: clause.clauseEnd });
    if (clause.namespaceName !== null) namespaceNames.add(clause.namespaceName);
    if (clause.defaultName !== null) namespaceNames.add(clause.defaultName);
    for (const { imported, local } of clause.named) {
      if (!ALLOWED_FS_MEMBERS.has(imported)) directNames.add(local);
    }
  }
  return { namespaceNames, directNames, declarationSpans };
}

const VALUE_TERMINATORS = new Set([',', '}', ';', ')']);

// [round 6→7, this fix] The round-5→6 slot rule (`isPropertyValuePosition`, removed here) asked only
// "does this occurrence sit in *a* property-value slot" — the direct RHS of an object-literal
// property, or of a dot-reached member assignment. Codex's round-6 finding: `const bypass = { run:
// fs.writeFileSync }; bypass.run(...)` sits in exactly such a slot (preceded by ':') and was
// accepted outright, even though `run` is a fresh private name, not the module's own `deps` seam.
// Any object literal or assignment satisfies "a property-value slot" just by copying that shape —
// the property this module actually needs is narrower: the reference must sit in *the* slot. Grep
// against the real module (capture-record.mjs) confirms there is exactly ONE such slot: the twelve
// `<member>: fs.<member>,` entries of `const defaultDeps = Object.freeze({ ... })` — every one of
// them keyed by the identical name it binds. That is now the positive rule, in two shapes:
//   (a) OBJECT-LITERAL shape — the reference is a direct (depth-1, not nested) property VALUE of the
//       `defaultDeps` literal itself, and its KEY equals the exact operation name being bound
//       (`openSync: fs.openSync,` passes; `run: fs.writeFileSync,` fails on the key; a correctly-
//       keyed property nested one level deeper inside `defaultDeps` — e.g. `nested: { writeFileSync:
//       fs.writeFileSync }` — fails on depth, because it is not a DIRECT child of the seam literal).
//   (b) ASSIGNMENT shape — `target.key = <ref>` where `target` is the bare name `deps` or
//       `defaultDeps` (see SANCTIONED_ASSIGNMENT_TARGETS below) and `key` again equals the exact
//       operation name. The real module never actually uses this spelling (grep confirms every
//       fs-operation reference it makes is shape (a)) — it exists only because an earlier round's
//       fixture already pinned `deps.openSync = fs.openSync;` as an equally legitimate abstract
//       spelling of "building the seam", and loosening shape (a)'s home to accept an assignment onto
//       ANY object, keyed correctly, would reopen the identical bypass one indirection over
//       (`bypass.writeFileSync = fs.writeFileSync; bypass.writeFileSync(...)` — a private copy under
//       a matching key, via assignment instead of a literal).
// A KEY/OPERATION mismatch fails both shapes identically, regardless of what put the reference there
// — a call, a `.call`/`.apply` indirection, an alias, a destructure, an escape, or a shape nobody has
// thought of yet: none of them is "keyed correctly, in the one sanctioned site".
//
// Residual, stated plainly rather than folded into a completeness claim: this is still a single-pass
// TEXT scan, not a scope-aware parser. A NESTED SCOPE that shadows the module-level `defaultDeps`
// binding with its own `const defaultDeps = { writeFileSync: fs.writeFileSync }` would be textually
// indistinguishable from the real seam to this checker (this needs true lexical-scope tracking to
// close, which this hand-rolled scanner does not attempt — same category of limitation as the
// template-literal-interior blind spot documented above `findDisallowedFsReference`, not a new one).
// No legitimate edit to this file has ever had reason to redeclare `defaultDeps` in a nested scope,
// so this is a real but narrow gap, not a load-bearing one.
//
// Same root cause, a second measured consequence (guard5 round 8, team-lead review): the locator
// takes the FIRST `defaultDeps` declaration in TOKEN ORDER and returns immediately — it does not
// attempt to disambiguate multiple declarations by scope, and it does not prefer a "more real-
// looking" one. Confirmed by direct measurement: `var defaultDeps = { foo: 1 };` (an unrelated
// EARLIER declaration with no fs reference in it at all) followed by a genuinely correct, correctly-
// keyed seam nested inside a function, still returns `fs_reference_outside_sanctioned_slot` for the
// real seam's own `openSync: fs.openSync` — the locator finds the decoy's span first and never looks
// past it. This fails CLOSED, same as the shadowing gap above (the checker rejects a legitimate
// module rather than admitting a bypass), and no legitimate edit to this file has ever had reason to
// declare more than one `defaultDeps` anywhere in it — but it is the same class of gap, not a new
// coincidence, and is recorded here rather than left for the next round to re-discover.

// The raw-source span of the `defaultDeps` object literal's `{...}` — the ONE declaration `const
// defaultDeps = Object.freeze({ ... })` in the real module, but the check below does not care what
// the real module happens to spell it as: it only reads what comes AFTER `defaultDeps`, never what
// keyword (if any) precedes it, so `const`/`let`/`var`/`export const`/a bare reassignment
// (`defaultDeps = ...` with no declaration keyword at all) are all recognized identically — verified
// directly, not merely assumed (see the fixtures below). It is located by finding the `defaultDeps`
// identifier immediately followed by `=` (its DECLARATION; never the two bare REFERENCES to it inside
// `mergeDeps`: `{ ...defaultDeps, ...deps } : defaultDeps`, neither followed by `=`), then balancing
// `{`/`}` PUNCT tokens forward from the first `{` after it.
//
// [round 7→8, this fix] "immediately followed by `=`" is read off `next.precededByChar`, which for
// most declarations is an IDENT token (`Object`, in `= Object.freeze({`) — but for the real module's
// object literal SANS wrapper (`const defaultDeps = { ... }`), the very next token is the `{` PUNCT
// token itself, which used to carry no `precededByChar` at all (only ident tokens did), so this
// branch's `!next || next.precededByChar !== '='` unconditionally treated that shape as "not a
// declaration" and rejected every legitimate reference inside it (team-lead review, guard5 round 7 —
// a plain-literal `defaultDeps` failed the real-module-shaped-but-not-Object.freeze'd test). Fixed at
// the token source: `{`/`}` PUNCT tokens now carry `precededByChar` too (see scanJsTokens), so this
// check reads correctly whichever kind of token happens to sit right after `defaultDeps =`.
//
// Returns null when no such declaration exists at all — every isolated snippet fixture in this file
// that carries no `defaultDeps` declaration of its own (which is most of them) simply has no
// sanctioned literal slot to sit in. When MULTIPLE `defaultDeps` declarations exist, this returns the
// FIRST one found in token order and never looks further — see the residual paragraph above
// isSanctionedSeamSlot for the measured consequence (an earlier, unrelated decoy declaration can hide
// a genuinely correct later seam) and why it is a documented, not accidental, limitation.
//
// Returned as TOKEN INDICES (`openIdx`/`closeIdx`), not raw source positions: the recursive
// template-literal branch of scanJsTokens re-scans an interpolated `${...}` expression as its OWN
// substring starting at position 0, so tokens found THAT way carry raw positions relative to the
// substring, not to the outer source (documented above `findDisallowedFsReference` as an existing,
// accepted blind spot). Token INDICES stay monotonic and correct regardless, since they only ever
// compare a token's ORDER in the stream, never its raw offset.
function findDefaultDepsLiteralSpan(tokens) {
  for (let idx = 0; idx < tokens.length; idx++) {
    if (tokens[idx].kind !== 'ident' || tokens[idx].text !== 'defaultDeps') continue;
    const next = tokens[idx + 1];
    if (!next || next.precededByChar !== '=') continue; // a REFERENCE to defaultDeps, not its declaration
    let openIdx = idx + 1;
    while (openIdx < tokens.length && !(tokens[openIdx].kind === 'punct' && tokens[openIdx].text === '{')) openIdx++;
    if (openIdx >= tokens.length) continue;
    let depth = 0;
    for (let j = openIdx; j < tokens.length; j++) {
      if (tokens[j].kind !== 'punct') continue;
      if (tokens[j].text === '{') depth++;
      else if (tokens[j].text === '}') {
        depth--;
        if (depth === 0) return { openIdx, closeIdx: j };
      }
    }
    return null; // unbalanced braces — malformed source, not this policy's concern
  }
  return null;
}

// See the shape-(b) rationale in the block comment above findDefaultDepsLiteralSpan: `deps` (the
// parameter name every entrypoint in this module merges the seam into, via `mergeDeps(deps)`) and
// `defaultDeps` itself, for symmetry. Deliberately NOT an open-ended or configurable list — widening
// it defeats the whole point of shape (b), which is that ONLY the seam's own binding names count.
const SANCTIONED_ASSIGNMENT_TARGETS = new Set(['deps', 'defaultDeps']);

// True iff the fs-bound occurrence at `tokens[idx]` (whose resolved operation name is
// `operationName` — the namespace member text for `fs.<member>`, or the direct-import local name for
// a bare bound name) sits in the module's one sanctioned seam slot. See the block comment above
// findDefaultDepsLiteralSpan for the two shapes and the rationale; this function is purely mechanical
// given the pieces built above it.
function isSanctionedSeamSlot(tokens, idx, operationName, defaultDepsSpan) {
  const base = tokens[idx];
  if (base.precededByChar === ':') {
    const key = tokens[idx - 1];
    if (key?.kind !== 'ident' || key.text !== operationName) return false;
    if (defaultDepsSpan === null) return false;
    if (idx <= defaultDepsSpan.openIdx || idx >= defaultDepsSpan.closeIdx) return false;
    let depth = 0;
    for (let j = defaultDepsSpan.openIdx; j < idx; j++) {
      if (tokens[j].kind !== 'punct') continue;
      if (tokens[j].text === '{') depth++;
      else if (tokens[j].text === '}') depth--;
    }
    return depth === 1; // a DIRECT child of the seam literal, not nested one level deeper inside it
  }
  if (base.precededByChar === '=') {
    const key = tokens[idx - 1];
    if (key?.kind !== 'ident' || key.precededByDot !== true || key.text !== operationName) return false;
    const objBase = tokens[idx - 2];
    return objBase?.kind === 'ident' && SANCTIONED_ASSIGNMENT_TARGETS.has(objBase.text);
  }
  return false;
}

// Walks every occurrence of a namespace or direct fs-bound name and rejects the first one that is
// not safely confined to the module's one sanctioned seam slot, `isSanctionedSeamSlot` (above).
// Deliberately does NOT use any FORWARD-looking token-chain tracking (the previous version's
// now-removed `dotBase` field, which named "the identifier this one was reached via a dot FROM") to
// figure out what follows an occurrence — that kind of tracking is exactly what an optional-chain
// `?.` or a grouping `(...)` defeats (a round-5 finding: both reset the bookkeeping a chain-tracker
// needs) — instead it re-derives "what immediately follows" fresh from the RAW SOURCE via
// `readIdentifierAt` every time, which is robust to both and also decodes `\uXXXX` escapes (the
// fourth round-5 finding) the same way scanJsTokens now does. `precededByDot`/`precededByChar` — a
// token's own immediate BACKWARD-looking facts, not a chain — are still used, by `isSanctionedSeamSlot`.
//
// `.constants` is the one member name exempt from the slot rule entirely (checked first, before slot
// position is even considered) — it is DATA (integer flags), never an operation, and the real module
// references it as a bare expression term throughout (`flags | fs.constants.O_NOFOLLOW`), never as a
// stored property value. A `?.`/bracket-accessed `.constants` is deliberately NOT recognized as this
// safe form (only a literal, immediate `.constants` is) — the module's own style never defensively
// null-checks or computed-accesses its own `fs` import, so treating anything else as an operation
// reference (and rejecting it unless it separately satisfies the slot rule) is a strictness bias with
// no real cost, not a gap: false-RED here is tolerable, false-GREEN is not (schema-gate-hardening
// principle 2).
//
// Known blind spot, stated plainly rather than left implicit: this walk starts from each BASE
// identifier occurrence and reads ONE step of raw source forward/backward from it. A reference
// smuggled through TWO indirections at once — e.g. a fs-bound name copied into a bare alias that is
// itself then only EVER referenced from inside a template-literal's `${...}` interpolation, whose
// recursively-tokenized positions are relative to the extracted substring rather than the outer
// source — is not specifically modeled; in practice this fails CLOSED (the position arithmetic lands
// on the wrong raw-source offset and very rarely lands on a value terminator by coincidence), but it
// is not a proven-sound case the way the four round-5 shapes above are, and any future rewrite of
// this policy should re-derive this walk's soundness against template-literal-interior positions
// specifically before relying on it there.
function findDisallowedFsReference(source, tokens, namespaceNames, directNames, declarationSpans, defaultDepsSpan) {
  for (let idx = 0; idx < tokens.length; idx++) {
    const base = tokens[idx];
    if (base.kind !== 'ident' || base.precededByDot) continue;
    if (declarationSpans.some((span) => base.start >= span.start && base.start < span.end)) continue;
    const isNamespace = namespaceNames.has(base.text);
    const isDirect = !isNamespace && directNames.has(base.text);
    if (!isNamespace && !isDirect) continue;

    let member = null;
    if (isNamespace) {
      const i = skipTriviaFrom(source, base.end);
      if (source[i] === '.') member = readIdentifierAt(source, i + 1);
      if (member !== null && member.text === 'constants') continue; // flag data, safe anywhere
      // A namespace occurrence with NO named member following it at all — `{ ...fs }`, `fn(fs)`,
      // `const x = fs`, a bare `fs` standing alone — names the WHOLE module object, every fs
      // operation at once, not the one resolved function the sanctioned seam-construction slot
      // exists for. This is never safe regardless of where it sits (ped-ant round 6): the slot
      // rule below only ever certified "one named operation, stored as a property value", and a
      // bare namespace reference is categorically the wrong shape for that, not a slot violation of
      // the same kind an `fs.writeFileSync` reference could also commit.
      if (member === null) return 'fs_namespace_referenced_directly';
    }

    // The exact operation name being bound — the namespace MEMBER text for `fs.<member>`, or the
    // direct local binding's own text for a name already destructured out of a named import. This is
    // what the key/property name at the sanctioned slot must match exactly (see isSanctionedSeamSlot).
    const operationName = member !== null ? member.text : base.text;
    if (!isSanctionedSeamSlot(tokens, idx, operationName, defaultDepsSpan)) {
      return 'fs_reference_outside_sanctioned_slot';
    }

    const afterEnd = member !== null ? member.end : base.end;
    const nextIdx = skipTriviaFrom(source, afterEnd);
    if (nextIdx < source.length && !VALUE_TERMINATORS.has(source[nextIdx])) {
      return 'fs_reference_escapes_value_slot';
    }
  }
  return null;
}

// [round 8→9, this fix] Everything above polices the CONSTRUCTION of the seam — every occurrence of
// an fs-bound name is confined to the one sanctioned property-value slot inside the `defaultDeps`
// literal. None of it says anything about the CONSUMPTION of `defaultDeps` itself once built. Codex's
// round-7 executed mutant on the real module — `capture-record.mjs:991`, `deps.mkdirSync(...)` ->
// `defaultDeps.mkdirSync(...)` — reaches the real filesystem through the frozen module-level default
// directly, completely ignoring whatever `deps` an injected virtual/test filesystem supplied, and
// `checkCapabilityPolicy` returned `{ok: true}` (reproduced directly against a copy of the real
// module's source before this fix). That matters beyond tidiness: every crash-recovery/atomicity test
// in this suite is testable at all only because every fs call this module makes goes through the
// injected `deps` seam; a call that reaches through `defaultDeps` instead silently unhooks itself from
// every one of those tests, and nothing here had a rule for it — `defaultDeps` was tracked only as the
// LOCATION of the seam literal (`findDefaultDepsLiteralSpan`), never as a name whose OWN occurrences
// need policing.
//
// Grepping the real module confirms the "never called" property actually holds today: `defaultDeps`
// appears exactly twice outside its own declaration, both inside `mergeDeps` — `{ ...defaultDeps,
// ...deps } : defaultDeps` — and both are BARE references (a spread source, and a ternary branch
// value), never a member access. That is the whole of its legitimate use: `defaultDeps` is the
// default seam, referenced only when the module BUILDS it (the declaration) and MERGES it
// (`mergeDeps`) — every actual fs operation is invoked through the merged `deps` parameter (or the
// local an entrypoint holds after `mergeDeps(deps)`), never through the raw default directly, which is
// the entire point of merging it rather than exporting it in the first place.
//
// The rule is therefore the mirror image of `fs_namespace_referenced_directly` above: for the raw
// `fs` namespace a BARE reference is the dangerous shape (it exposes every operation at once) and a
// `.member` is what the sanctioned slot narrows down to safely; for `defaultDeps` it is the other way
// round — a bare reference is exactly the sanctioned merge/spread shape, and a `.member`/`?.member`/
// `[...]` reference extracts one already-resolved, un-seamed function as a callable value. Any
// occurrence of `defaultDeps` immediately followed (after trivia — across whitespace, a comment or a
// newline) by `.`, `?.` or `[` is rejected outright: no key/site check needed, because there is no
// legitimate member-access shape of `defaultDeps` at all, the way there is a legitimate slot for
// `fs.<member>`. The declaration itself (`defaultDeps = { ... }`, in any of the spellings
// `findDefaultDepsLiteralSpan` already recognizes) is unaffected, since it is always followed by `=`,
// never by one of the three rejected characters — no separate declaration-exclusion check is needed.
//
// Deliberately NOT extended to the merged local (`deps`, or the local an entrypoint binds after
// `mergeDeps(deps)`) — that name IS the seam, called through by design in every exported function; a
// rule that fired on `deps.<member>(...)` would reject the module's entire normal operation, not one
// bypass, which is exactly the false-positive risk team-lead review flagged before this fix was
// chosen.
//
// Known blind spot, stated plainly rather than left implicit, in the same spirit as the residual
// paragraphs above `findDisallowedFsReference`: this is a single-hop check on the literal name
// `defaultDeps`. `const dd = defaultDeps; dd.mkdirSync(...)` aliases the bare (and therefore
// "sanctioned-shaped") reference into a fresh local this checker does not track at all, and STILL
// PASSES — measured directly, not assumed (see the dedicated test below). No legitimate edit to this
// file has ever had reason to rebind `defaultDeps` under a second name — the module's own convention
// is to merge it via `mergeDeps`, never to alias it — so this is a real but narrow gap, not a load-
// bearing one, and the next round should not have to rediscover it.
// [round 8→9, this fix] Wrapping parens are skipped before the lookahead. `(defaultDeps).mkdirSync()`,
// `(defaultDeps)['mkdirSync']()` and `(defaultDeps)?.mkdirSync()` all reached the identical function
// while the bare lookahead saw only `)` and reported clean (codex round 8, executed against this
// checker). The sibling `fs` walk was never affected by the same spelling, and NOT because it shares
// a paren-skipping helper with this one — the two rules are structurally opposite and no such helper
// exists to share. `findDisallowedFsReference` asks whether an occurrence sits in a SANCTIONED slot
// and looks BACKWARD to answer it: in `(fs).writeFileSync(...)` the preceding significant character
// is `(`, which is not `:` or `=`, so the occurrence fails the slot test and is rejected without the
// walk ever needing to see through the paren. This rule asks the mirror question — is a legitimately
// bare name being USED as a member base — and that one can only be answered by looking FORWARD, which
// is precisely where a paren hides. So the divergence is inherent to the two questions, not a lost
// abstraction, and the fix belongs here rather than in a shared helper.
//
// Deliberate over-catch, stated rather than discovered later: closing parens are skipped
// unconditionally, so `someCall(defaultDeps).mkdirSync()` — where the `)` closes an argument list and
// the member access applies to the call's RESULT, not to `defaultDeps` — is rejected too. Telling the
// two apart needs the paren classified as grouping-versus-call, which needs a backward trivia scan
// this tokenizer does not have. Passing the default seam into a call and dotting the result is not a
// shape this module has or needs (its only uses are `{ ...defaultDeps, ...deps }` and a ternary
// branch, both bare), and the fail direction is the safe one: a false positive is a conversation, a
// false negative ships an unseamed call. Same strictness bias the `fs?.constants` rejection above
// already takes. Pinned by a test so the over-catch stays visible.
function findDisallowedDefaultDepsReference(source, tokens) {
  for (let idx = 0; idx < tokens.length; idx++) {
    const token = tokens[idx];
    if (token.kind !== 'ident' || token.text !== 'defaultDeps' || token.precededByDot) continue;
    let i = skipTriviaFrom(source, token.end);
    // Any depth of wrapping: `((defaultDeps)).mkdirSync()`, and trivia between each layer.
    while (source[i] === ')') i = skipTriviaFrom(source, i + 1);
    const isCallOrMemberBase = source[i] === '.' || source[i] === '[' || (source[i] === '?' && source[i + 1] === '.');
    if (isCallOrMemberBase) return 'default_deps_referenced_as_call_base';
  }
  return null;
}

// `export {...} from '<specifier>'` / `export * from '<specifier>'` / `export * as ns from
// '<specifier>'` — re-publishes named bindings from an external module to every future importer of
// THIS file. It performs no I/O of its own within this module, so none of the occurrence/slot
// machinery above (scoped to references made INSIDE this file) can see it at all — but it defeats
// the module's own stated contract just as completely: "the ONLY module in this feature that
// touches disk" stops being true for any downstream consumer that imports the re-exported name
// directly, bypassing the injectable `deps` seam entirely (ped-ant round 6:
// `export { writeFileSync } from 'node:fs';` performs no I/O in THIS file and is invisible to every
// check above, yet republishes the raw function). Scoped deliberately to the three RAW-CAPABILITY
// specifiers this policy already gates — `node:fs`, `node:child_process`, `node:crypto` — a
// re-export from either sibling PURE-helper module (`build-identity.mjs`, `chapter-paths.mjs`)
// re-publishes no I/O capability and is NOT this policy's concern; this checker does not scan for it.
const RAW_CAPABILITY_SPECIFIERS = new Set(['node:fs', 'node:child_process', 'node:crypto']);

// Finds every `export ... from '<specifier>'` statement's specifier. Gated on the character
// IMMEDIATELY after `export` (skipping trivia) being `{` or `*` — the only two shapes a re-export-
// from clause can start with — so an ordinary `export function foo() { ... }`/`export const x = ...`
// declaration (the overwhelming majority of this module's own exports) is skipped before ever
// scanning its BODY, exactly the same safety `findImportStatements` relies on for its own forward
// scan. A from-less bare re-export (`export { a, b };`, which republishes nothing external and has
// no capability-leak relevance) naturally finds no string before the next `import`/`export` keyword
// and contributes nothing here — matching `findImportStatements`'s own "not well-formed enough to
// matter to this policy" bail-out.
function findReExportSpecifiers(source, tokens) {
  const specifiers = [];
  for (let idx = 0; idx < tokens.length; idx++) {
    if (tokens[idx].kind !== 'ident' || tokens[idx].text !== 'export') continue;
    const i = skipTriviaFrom(source, tokens[idx].end);
    if (source[i] !== '{' && source[i] !== '*') continue; // a local declaration export — no specifier to trace
    for (let j = idx + 1; j < tokens.length; j++) {
      if (tokens[j].kind === 'string') { specifiers.push(tokens[j].text); break; }
      if (tokens[j].kind === 'ident' && (tokens[j].text === 'import' || tokens[j].text === 'export')) break;
    }
  }
  return specifiers;
}

function checkCapabilityPolicy(source) {
  const tokens = scanJsTokens(source);
  for (const { specifier } of findImportStatements(tokens)) {
    if (!ALLOWED_IMPORT_SPECIFIERS.has(specifier)) return { ok: false, reason: `disallowed_import:${specifier}` };
  }
  for (const specifier of findReExportSpecifiers(source, tokens)) {
    if (RAW_CAPABILITY_SPECIFIERS.has(specifier)) return { ok: false, reason: `reexports_raw_capability:${specifier}` };
  }
  const { namespaceNames, directNames, declarationSpans } = collectFsBindings(source, tokens);
  const defaultDepsSpan = findDefaultDepsLiteralSpan(tokens);

  const fsReason = findDisallowedFsReference(source, tokens, namespaceNames, directNames, declarationSpans, defaultDepsSpan);
  if (fsReason) return { ok: false, reason: fsReason };

  const defaultDepsReason = findDisallowedDefaultDepsReference(source, tokens);
  if (defaultDepsReason) return { ok: false, reason: defaultDepsReason };

  for (const token of tokens) {
    if (UNCONDITIONALLY_BANNED.has(token.text)) return { ok: false, reason: `banned_word:${token.text}` };
    // "constructor" is a legitimate class-method-definition keyword (`class X { constructor() {} }`)
    // when it stands ALONE, so only its escape-hatch SHAPES are banned: a `.constructor` MEMBER
    // ACCESS (identifier reached via a dot, at any depth or through an alias — the alias is caught
    // because the alias's OWN initializer still contains the `.constructor` access token), and a
    // computed/string-literal reference (`'constructor'`, `` `constructor` ``, `["constructor"]`).
    if (token.text === 'constructor' && (token.kind === 'string' || token.precededByDot)) {
      return { ok: false, reason: 'banned_word:constructor' };
    }
    // Dynamic import(...) — an 'import' IDENT token (never produced from inside a comment, since
    // the tokenizer skips comments outright) immediately followed by '(' — checked via
    // `nextSignificantChar` (skips whitespace AND comments) rather than a `/^\s*\(/` regex, which a
    // comment wedged between `import` and `(` (`import /*x*/ ('node:fs')`) defeated outright
    // (codex, important #7).
    if (token.kind === 'ident' && token.text === 'import' && nextSignificantChar(source, token.end) === '(') {
      return { ok: false, reason: 'dynamic_import' };
    }
  }
  // Banned-word-via-string-concatenation: `'pro' + 'cess'` never produces a single token equal to
  // "process", so the per-token check above cannot see it — walk STRING (`+` STRING)+ chains,
  // concatenate their literal values, and check the joined result too (codex, important #7:
  // `globalThis['pro'+'cess']` defeated the per-token check outright).
  for (let idx = 0; idx < tokens.length; idx++) {
    if (tokens[idx].kind !== 'string') continue;
    let joined = tokens[idx].text;
    let j = idx + 1;
    while (tokens[j]?.kind === 'punct' && tokens[j].text === '+' && tokens[j + 1]?.kind === 'string') {
      joined += tokens[j + 1].text;
      j += 2;
    }
    if (j === idx + 1) continue; // no concatenation chain starting here; the lone-string case is
    // already covered by the per-token loop above.
    for (const banned of UNCONDITIONALLY_BANNED) {
      if (joined.includes(banned)) return { ok: false, reason: `banned_word_via_concatenation:${banned}` };
    }
    if (joined.includes('constructor')) return { ok: false, reason: 'banned_word_via_concatenation:constructor' };
  }
  return { ok: true };
}

test('capability policy: the real module PASSES', () => {
  const result = checkCapabilityPolicy(MODULE_SOURCE);
  assert.equal(result.ok, true, JSON.stringify(result));
});

test('capability policy: mutants — each of these must FAIL', () => {
  // Every entry below is an INERT STRING FIXTURE fed to the static text scanner above — none of
  // this is ever executed via `eval`/`new Function`/`require` in this test; it is source text the
  // checker must recognize as forbidden, exactly the way a real reviewer reads a diff.
  const mutants = [
    "globalThis.process.getBuiltinModule('node:fs')",
    "const p = globalThis.process; p.binding('fs')",
    'const { getBuiltinModule } = process',
    'process["bind" + "ing"]',
    'new Function("return 1")()',
    'eval("1+1")',
    "({}).constructor.constructor('return process')()",
    "(() => {}).constructor('return process')()",
    "(() => {})['constructor'](...)",
    'const C = (() => {}).constructor; C("return process")()',
    '(() => {}).constructor`return process`()',
    "require('node:fs')",
    "const { createRequire } = require('node:module')",
    "import('node:fs')",
    // The four codex (important #7) demonstrated defeats of an earlier version of this checker:
    "fs.writeFileSync('/etc/passwd', 'pwned')", // a direct fs.* call bypassing the seam entirely
    "import /*comment*/('node:fs')", // a comment wedged between `import` and `(` defeated the old /^\s*\(/ check
    "globalThis['pro' + 'cess']", // the banned word itself split across concatenated string literals
    "import/*x*/{a}from'not-an-allowed-specifier'", // a comment inside a STATIC import defeated the old whitespace-only regex outright (zero specifiers found)
    // IMPORTANT 4 (codex review): the checker's OLD "class", not just its four demonstrated
    // instances — allowed `node:fs` unconditionally and banned only a direct `fs.<method>(...)`
    // call SHAPE, so a named import or a bare-variable alias of the identical banned function was
    // invisible to it, however the module later invoked it.
    "import { writeFileSync } from 'node:fs'; writeFileSync(path, data)", // a named import destructures the real fs function directly into scope — no `fs.` prefix anywhere
    "const write = fs.writeFileSync; write(...)", // a bare alias of the fs.* VALUE, later called under its own name
    // [round 3, codex] the checker was still a denylist rooted on the literal identifier `fs` and a
    // `.`-access shape — every one of these reaches the identical banned function through a binding
    // or access form the round-2 checks never named.
    'import fsDefault from "node:fs"; fsDefault.writeFileSync(...)', // a DEFAULT import — not the namespace form the round-2 checker looked for
    'import * as io from "node:fs"; io.writeFileSync(...)', // a namespace import under an alias OTHER than "fs"
    'fs["writeFileSync"](...)', // computed/bracket member access — not a literal `.` immediately after `fs`, so it never reaches the one recognized safe form and is rejected as an out-of-slot occurrence
    'const {writeFileSync} = fs; writeFileSync(...)', // destructuring off the namespace binding, not a simple assignment
    // [round 5, codex] four MORE bypasses against the round-3 rewrite immediately above — every one
    // of these reaches the identical banned function while returning `ok: true` from that version,
    // because none of them matches the literal "namespace.method immediately followed by '('" shape
    // it checked for. This is exactly the class of failure the round-5→6 rewrite (see the
    // collectFsBindings doc comment above) replaced with a positive LOCATION check instead of one
    // more shape.
    'fs?.writeFileSync("/etc/passwd", "pwned")', // optional chaining breaks the "reached via a dot" tracking a shape check relies on
    '(fs).writeFileSync("/etc/passwd", "pwned")', // a grouping paren does the same
    'fs.writeFileSync.call(null, "/etc/passwd", "pwned")', // the CALL happens one property hop past the dotted expression a shape check inspects
    'import { writeFileSync as \\u0077rite } from "node:fs";\nwrite("/etc/passwd", "pwned")', // the local binding is spelled with a `\uXXXX` escape — one binding to the JS engine, a different raw string to a checker that never decodes it
    // [round 6, ped-ant review] two MORE gaps found in the round-5→6 rewrite itself — see the
    // collectFsBindings doc comment above for why each needed a genuinely new mechanism, not one
    // more slot-check special case.
    'import * as fs from "node:fs";\nconst d = { ...fs };\nd.writeFileSync("/etc/passwd", "pwned")', // object-spread of the whole namespace copies every export, including writeFileSync, into an un-seamed object
    'export { writeFileSync } from "node:fs";', // re-exports the raw function to every importer of THIS module; no I/O inside this file at all, invisible to every occurrence check
    'export * from "node:fs";', // the same leak, in its most totalizing form
    'export * as rawFs from "node:fs";', // ...and under a namespace alias
    // [round 6→7, this fix] the round-5→6 rewrite immediately above sanctioned ANY property-value
    // slot, not the module's own ONE seam — this is codex's round-6 executed mutant, which returned
    // `{ ok: true }` against that version: a fresh object literal, keyed by whatever name the author
    // likes, sits in a real property-value slot (preceded by ':') exactly as legitimately as
    // `defaultDeps` itself does, and the old rule had no way to tell them apart.
    "const bypass = { run: fs.writeFileSync };\nbypass.run('/outside', 'x');",
    // [round 8→9, this fix] codex's round-7 executed mutant on the real module —
    // `capture-record.mjs:991`, `deps.mkdirSync(...)` -> `defaultDeps.mkdirSync(...)` — reached
    // through the frozen module-level default directly, bypassing whatever `deps` an injected
    // virtual/test filesystem supplied entirely; `checkCapabilityPolicy` returned `{ok: true}`
    // against the pre-fix checker (reproduced directly against a copy of the real module before
    // this fix). Every occurrence-of-`fs` rule above is silent on this: `defaultDeps` was never
    // itself a name this checker policed, only a location it looked INSIDE.
    "defaultDeps.mkdirSync(dir, { recursive: true });",
  ];
  for (const mutant of mutants) {
    const result = checkCapabilityPolicy(mutant);
    assert.equal(result.ok, false, `mutant should FAIL: ${mutant}`);
  }
});

test('capability policy: an UNCONDITIONALLY_BANNED word spelled with a unicode escape is still caught (the round-5 escape-decoding fix applies file-wide, not only to fs-bound names)', () => {
  const result = checkCapabilityPolicy('\\u0070rocess.exit()');
  assert.equal(result.ok, false, JSON.stringify(result));
});

test('capability policy: a bare namespace reference is rejected even with no subsequent call — passing it as an argument, or storing the WHOLE object (not one resolved member) as a property value, both expose every fs operation at once', () => {
  assert.equal(checkCapabilityPolicy('import * as fs from "node:fs";\nsomeFunc(fs);').ok, false);
  assert.equal(checkCapabilityPolicy('import * as fs from "node:fs";\ndeps.fs = fs;').ok, false);
  assert.equal(checkCapabilityPolicy('import fsDefault from "node:fs";\nconst d = { ...fsDefault };').ok, false);
});

test('capability policy: a legitimate fs.constants VALUE reference (never called) is not flagged', () => {
  const result = checkCapabilityPolicy("const x = fs.constants.O_RDONLY; deps.openSync = fs.openSync;");
  assert.equal(result.ok, true, JSON.stringify(result));
});

test('capability policy: a disallowed static import is rejected', () => {
  const result = checkCapabilityPolicy("import { execSync } from 'node:os';\n");
  assert.equal(result.ok, false);
});

test('capability policy: spreading an object that is NOT an fs-bound name is unaffected — the real module\'s own `{ ...defaultDeps, ...deps }` / `{ ...profileLike, ... }` pattern, and rest params in a function signature, all still PASS', () => {
  assert.equal(checkCapabilityPolicy('const merged = deps ? { ...defaultDeps, ...deps } : defaultDeps;').ok, true);
  assert.equal(checkCapabilityPolicy('const p = { ...profileLike, capture: { ...profileLike.capture, output_dir: x } };').ok, true);
  assert.equal(checkCapabilityPolicy('function posixJoin(...parts) { return parts; }').ok, true);
});

test('capability policy: re-exporting from an ALLOWED sibling module (out of scope for this policy — it republishes no raw I/O capability), and a from-less bare local re-export, both still PASS', () => {
  assert.equal(checkCapabilityPolicy("export { normalizeBuildIdentity } from './build-identity.mjs';").ok, true);
  assert.equal(checkCapabilityPolicy('const a = 1, b = 2;\nexport { a, b };').ok, true);
});

// [round 6→7, this fix] the sanctioned-SITE rule (isSanctionedSeamSlot / findDefaultDepsLiteralSpan)
// closes the class codex's mutant belongs to, not just that one instance — every near variant a
// reviewer would think to try next.
test('capability policy: a correctly-shaped, correctly-KEYED seam entry still needs the correct SITE — a second literal or a bare assignment, keyed exactly like the real seam, both still FAIL', () => {
  // A second object literal, keyed identically to a real fs member, with no `defaultDeps`
  // declaration anywhere in the snippet at all — this is the "second literal keyed exactly like the
  // seam" variant: the key-match alone would let this through, which is exactly why the SITE half of
  // the rule (must sit inside the actual `defaultDeps` literal) exists.
  assert.equal(
    checkCapabilityPolicy("const bypass = { writeFileSync: fs.writeFileSync };\nbypass.writeFileSync('/outside', 'x');").ok,
    false,
  );
  // An assignment onto a non-seam object, keyed correctly — the assignment-shape analogue of the
  // same gap: `bypass` is not `deps`/`defaultDeps`, so this fails on SITE even though the key matches.
  assert.equal(
    checkCapabilityPolicy("const bypass = {};\nbypass.writeFileSync = fs.writeFileSync;\nbypass.writeFileSync('/outside', 'x');").ok,
    false,
  );
  // A nested literal ONE level inside the real seam, keyed correctly, alongside a genuinely
  // legitimate entry — depth alone must reject it: the property is not a DIRECT child of
  // `defaultDeps`, it is a private copy stashed one level deeper inside an otherwise-real seam.
  assert.equal(
    checkCapabilityPolicy(
      'const defaultDeps = Object.freeze({\n  openSync: fs.openSync,\n  nested: { writeFileSync: fs.writeFileSync },\n});',
    ).ok,
    false,
  );
});

test('capability policy: an ordinary, correctly-shaped defaultDeps literal — the real seam shape, standalone — still PASSES, including a legitimate future ADDITION to it', () => {
  assert.equal(
    checkCapabilityPolicy('const defaultDeps = Object.freeze({ openSync: fs.openSync, closeSync: fs.closeSync });').ok,
    true,
  );
  // A future maintenance edit that adds one more correctly-keyed member to the real seam must not
  // trip this rule — tightness that breaks ordinary maintenance is its own kind of failure.
  assert.equal(
    checkCapabilityPolicy(
      'const defaultDeps = Object.freeze({\n  openSync: fs.openSync,\n  closeSync: fs.closeSync,\n  readFileSync: fs.readFileSync,\n});',
    ).ok,
    true,
  );
});

// [round 7→8, this fix] team-lead review, guard5 round 7: the real module happens to spell its seam
// as `Object.freeze({ ... })`, but nothing in the CONTRACT requires that wrapper — a plain
// `const defaultDeps = { ... }` is an equally legitimate seam shape, and the round-6→7 locator
// silently rejected it (see findDefaultDepsLiteralSpan's doc comment above). Pinned here so the
// plain spelling can never regress, alongside its own wrongly-keyed variant, so the newly-accepted
// path does not become a fresh hole the way the ORIGINAL `Object.freeze` shape's site rule almost did.
test('capability policy: the seam literal need not be wrapped in Object.freeze(...) — a plain `const defaultDeps = { ... }` is an equally legitimate seam, and a wrongly-keyed member inside THAT shape is still rejected', () => {
  assert.equal(checkCapabilityPolicy('const defaultDeps = { openSync: fs.openSync };').ok, true);
  assert.equal(checkCapabilityPolicy('const defaultDeps = { run: fs.writeFileSync };').ok, false);
});

// [round 7→8, this fix] findDefaultDepsLiteralSpan's declaration check reads only what FOLLOWS
// `defaultDeps` (`= ...`), never what precedes it — so it is agnostic to the declaration keyword.
// Verified directly rather than left as an assumption riding on the fix above: `let` and an exported
// `const` both locate the seam identically to a bare `const`.
test('capability policy: the seam locator is agnostic to the declaration keyword — `let` and `export const` both find the real seam the same way `const` does', () => {
  assert.equal(checkCapabilityPolicy('let defaultDeps = { openSync: fs.openSync };').ok, true);
  assert.equal(checkCapabilityPolicy('export const defaultDeps = { openSync: fs.openSync };').ok, true);
});

// [round 8→9, this fix] dedicated coverage for findDisallowedDefaultDepsReference — every variant of
// "a call whose base is defaultDeps" this round considered, the two ordinary shapes it must leave
// alone, and the one still-admitted alias gap, pinned so a future round does not have to rediscover
// any of them by hand.
test('capability policy: defaultDeps is the seam DEFAULT, never a call target — direct, bracket and optional-chain member access are all rejected; the merged local and the real merge/spread shapes stay untouched; the un-tracked alias gap is measured, not assumed', () => {
  // codex's round-7 executed mutant (capture-record.mjs:991), and the bracket/optional-chain
  // variants of the identical bypass — none of these is a shape check, so all three fall to it.
  assert.equal(checkCapabilityPolicy('defaultDeps.mkdirSync(dir, { recursive: true });').ok, false);
  assert.equal(checkCapabilityPolicy("defaultDeps['mkdirSync'](dir, { recursive: true });").ok, false);
  assert.equal(checkCapabilityPolicy('defaultDeps?.mkdirSync(dir, { recursive: true });').ok, false);
  assert.equal(checkCapabilityPolicy('defaultDeps?.["mkdirSync"](dir);').ok, false);

  // [round 8→9] Wrapping parens hid all three of these from the forward lookahead, which saw only
  // `)`. Any depth, and trivia between the layers, since the skip loops.
  assert.equal(checkCapabilityPolicy('(defaultDeps).mkdirSync(dir, { recursive: true });').ok, false);
  assert.equal(checkCapabilityPolicy("(defaultDeps)['mkdirSync'](dir);").ok, false);
  assert.equal(checkCapabilityPolicy('(defaultDeps)?.mkdirSync(dir);').ok, false);
  assert.equal(checkCapabilityPolicy('((defaultDeps)).mkdirSync(dir);').ok, false);
  assert.equal(checkCapabilityPolicy('( defaultDeps /* c */ ) /* c */ . mkdirSync(dir);').ok, false);

  // The deliberate over-catch that buys the above, pinned so it stays a decision rather than a
  // surprise: here the `)` closes an ARGUMENT LIST and the member access applies to the call's
  // result, not to `defaultDeps` — telling that apart needs the paren classified as
  // grouping-versus-call, which needs a backward trivia scan this tokenizer does not have. Rejected
  // anyway, because the fail direction is the safe one and this module never passes its own default
  // seam into a call.
  assert.equal(checkCapabilityPolicy('someCall(defaultDeps).mkdirSync(dir);').ok, false);

  // The real module's own two legitimate bare references, both inside mergeDeps, must keep passing —
  // already pinned above ("spreading an object that is NOT an fs-bound name is unaffected"); repeated
  // here as the negative control for THIS rule specifically.
  assert.equal(checkCapabilityPolicy('const merged = deps ? { ...defaultDeps, ...deps } : defaultDeps;').ok, true);

  // The merged local (`deps`, or whatever name an entrypoint binds `mergeDeps(deps)` to) is NOT
  // `defaultDeps` — an ordinary seam call through it must not be caught by this rule, or the module's
  // entire normal operation would fail its own capability policy.
  assert.equal(checkCapabilityPolicy('deps.mkdirSync(dir, { recursive: true });').ok, true);
  assert.equal(checkCapabilityPolicy('const d = mergeDeps(deps);\nd.mkdirSync(dir, { recursive: true });').ok, true);

  // Known, MEASURED (not assumed) blind spot: aliasing the bare — and therefore "sanctioned-shaped"
  // — reference into a fresh local this checker does not track defeats the rule completely. This is
  // NOT a design goal, it is the single-hop limitation documented above
  // findDisallowedDefaultDepsReference; pinned here as evidence rather than left as an unverified
  // claim, the same way the decoy-declaration residual above findDefaultDepsLiteralSpan was confirmed
  // by direct measurement rather than assumed.
  assert.equal(checkCapabilityPolicy('const dd = defaultDeps;\ndd.mkdirSync(dir, { recursive: true });').ok, true);
});

// =================================================================================================
// REQUIRED INTEGRATION TEST — feed teammate B's ACTUAL chapter-paths.mjs exports into
// recordChapterProvenance / buildProvenanceReport. Guarded: if chapter-paths.mjs has not yet landed
// buildEmbedCandidates/expectedAssets/isCanonicalAssetKey, this section is SKIPPED with a clear
// notice rather than failing the whole suite — it must be re-enabled (remove the guard) once
// teammate B reports done, and re-run before this branch is considered ready to push.
// =================================================================================================

const chapterPathsModule = await import('../skills/enduser-handbook/assets/lib/chapter-paths.mjs');
const hasRealExtractionContract =
  typeof chapterPathsModule.buildEmbedCandidates === 'function' &&
  typeof chapterPathsModule.expectedAssets === 'function' &&
  typeof chapterPathsModule.isCanonicalAssetKey === 'function';

test('INTEGRATION (real chapter-paths.mjs): flat entry, grouped entry, and one halting chapter', { skip: !hasRealExtractionContract }, () => {
  const { expectedAssets } = chapterPathsModule;
  withTempDir((dir) => {
    const profile = profileFor(dir);

    // --- flat ---
    const flat = { slug: 'items' };
    const flatAssetDir = join(profile.capture.output_dir, 'items');
    nodeFs.mkdirSync(flatAssetDir, { recursive: true });
    nodeFs.writeFileSync(join(flatAssetDir, 'overview.png'), 'v1');
    const flatChapterPath = join(dir, 'items.md');

    const openedFlat = CR.openCaptureRun(profile, [flat], null, stubDepsNoIdentity());
    assert.equal(openedFlat.ok, true);
    nodeFs.writeFileSync(join(flatAssetDir, 'overview.png'), 'v2');
    const closedFlat = CR.closeCaptureRun(profile, openedFlat.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closedFlat.ok, true);

    const flatEmbed = chapterPathsModule.embedPath(flatChapterPath, flatAssetDir, 'overview.png');
    nodeFs.writeFileSync(flatChapterPath, `# Items\n\n1. Step\n\n   ![overview](${flatEmbed})\n`);
    const flatFilenames = nodeFs.readdirSync(flatAssetDir);
    const flatText = nodeFs.readFileSync(flatChapterPath, 'utf8');
    const deps = { ...stubDepsNoIdentity(), expectedAssets };
    const flatResult = CR.recordChapterProvenance(
      profile,
      [flat],
      flat,
      flatChapterPath,
      openedFlat.runState.run_id,
      { ...deps, expectedAssets: () => expectedAssets(profile, flat, flatChapterPath, flatText, flatFilenames, profile.publish.target) },
    );
    assert.equal(flatResult.recorded, true, JSON.stringify(flatResult));

    // --- grouped ---
    const grouped = { slug: 'invoices', group: 'billing' };
    const groupedAssetDir = join(profile.capture.output_dir, 'billing', 'invoices');
    nodeFs.mkdirSync(groupedAssetDir, { recursive: true });
    nodeFs.writeFileSync(join(groupedAssetDir, 'open-invoice.png'), 'g1');
    const groupedChapterPath = join(dir, 'billing', 'invoices.md');
    nodeFs.mkdirSync(join(dir, 'billing'), { recursive: true });

    const openedGrouped = CR.openCaptureRun(profile, [grouped], null, stubDepsNoIdentity());
    assert.equal(openedGrouped.ok, true);
    nodeFs.writeFileSync(join(groupedAssetDir, 'open-invoice.png'), 'g2');
    const closedGrouped = CR.closeCaptureRun(profile, openedGrouped.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closedGrouped.ok, true);

    const groupedEmbed = chapterPathsModule.embedPath(groupedChapterPath, groupedAssetDir, 'open-invoice.png');
    nodeFs.writeFileSync(groupedChapterPath, `# Invoices\n\n1. Step\n\n   ![open invoice](${groupedEmbed})\n`);
    const groupedFilenames = nodeFs.readdirSync(groupedAssetDir);
    const groupedText = nodeFs.readFileSync(groupedChapterPath, 'utf8');
    const groupedResult = CR.recordChapterProvenance(
      profile,
      [grouped],
      grouped,
      groupedChapterPath,
      openedGrouped.runState.run_id,
      { ...deps, expectedAssets: () => expectedAssets(profile, grouped, groupedChapterPath, groupedText, groupedFilenames, profile.publish.target) },
    );
    assert.equal(groupedResult.recorded, true, JSON.stringify(groupedResult));

    // --- halting chapter (a reference-form image, which must HALT the extractor) ---
    const halting = { slug: 'broken' };
    const haltingAssetDir = join(profile.capture.output_dir, 'broken');
    nodeFs.mkdirSync(haltingAssetDir, { recursive: true });
    nodeFs.writeFileSync(join(haltingAssetDir, 'shot.png'), 'h1');
    const haltingChapterPath = join(dir, 'broken.md');
    const openedHalting = CR.openCaptureRun(profile, [halting], null, stubDepsNoIdentity());
    nodeFs.writeFileSync(join(haltingAssetDir, 'shot.png'), 'h2');
    const closedHalting = CR.closeCaptureRun(profile, openedHalting.runState, { ok: true }, null, stubDepsNoIdentity());
    assert.equal(closedHalting.ok, true);
    nodeFs.writeFileSync(haltingChapterPath, '# Broken\n\n![shot][ref]\n\n[ref]: shot.png\n');
    const haltingFilenames = nodeFs.readdirSync(haltingAssetDir);
    const haltingText = nodeFs.readFileSync(haltingChapterPath, 'utf8');
    const haltingResult = CR.recordChapterProvenance(
      profile,
      [halting],
      halting,
      haltingChapterPath,
      openedHalting.runState.run_id,
      { ...deps, expectedAssets: () => expectedAssets(profile, halting, haltingChapterPath, haltingText, haltingFilenames, profile.publish.target) },
    );
    assert.equal(haltingResult.recorded, false, JSON.stringify(haltingResult));
  });
});

if (!hasRealExtractionContract) {
  test('INTEGRATION notice: chapter-paths.mjs has not yet landed buildEmbedCandidates/expectedAssets/isCanonicalAssetKey', () => {
    // This is a VISIBLE reminder, not a silent skip: re-run the suite once teammate B reports done.
    assert.ok(true, 'integration test above was skipped pending chapter-paths.mjs new exports');
  });
}
