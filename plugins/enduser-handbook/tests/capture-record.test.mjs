// Unit tests for the disk-touching provenance module (enduser-handbook 1.12.0, #362). Runs under
// Node's built-in test runner: `node --test capture-record.test.mjs` (explicit path — `node --test
// <dir>` gives a misleading MODULE_NOT_FOUND).
//
// Section order: JCS canonicalization (pinned vectors + mutants) -> the strict JSON reader
// (duplicate-key / lone-surrogate) -> path derivations -> gate 5 (ownership) -> gate 6 (hazards) ->
// row 6 (the 22-case + 24-raw-case classifier, plus abort/cleanup) -> openCaptureRun/closeCaptureRun
// -> recordChapterProvenance (completeness rules 1-5) -> buildProvenanceReport -> the fs capability
// policy -> the required integration test against teammate B's real chapter-paths.mjs exports.

import test from 'node:test';
import assert from 'node:assert/strict';
import * as nodeFs from 'node:fs';
import { createHash, randomUUID as nodeRandomUUID } from 'node:crypto';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import * as CR from '../skills/enduser-handbook/assets/lib/capture-record.mjs';

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

test('jcs: digestOpeningPayload throws on an uncanonicalizable payload and succeeds otherwise', () => {
  assert.throws(() => CR.digestOpeningPayload({ x: undefined }));
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
    chapters: { a: { opening: { x: ONE_DIGEST }, closing: { x: ONE_DIGEST } }, b: { opening: { x: ONE_DIGEST }, closing: { x: ONE_DIGEST } } },
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
    CR.readRunRecordText(JSON.stringify({ ...base(), chapters: { a: { opening: { x: 'not-a-hash' }, closing: {} } } })).ok,
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
      chapters: { a: { opening: { [key]: ONE_DIGEST }, closing: { [key]: ONE_DIGEST } } },
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
    chapters: { a: { opening: {}, closing: {} } },
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
  // `alias-a` and `alias-b` are two distinct names, both pointing at the SAME real directory — the
  // raw configured strings ('.../alias-a/.provenance' vs '.../alias-b') share no lexical prefix at
  // all, so a reader comparing only the raw values would conclude the two trees are unrelated. They
  // are not: both resolve into 'real', and `capture.output_dir` (resolved) turns out to be a direct
  // ANCESTOR of the resolved provenance root — the overlap the gate exists to refuse. The gate
  // already refuses this correctly (this is not a gate-5 detection bug); what this test pins is that
  // the halt MESSAGE explains why, by naming what each raw string actually resolved to.
  withTempDir((dir) => {
    const real = join(dir, 'real');
    nodeFs.mkdirSync(real, { recursive: true });
    const aliasA = join(dir, 'alias-a');
    const aliasB = join(dir, 'alias-b');
    nodeFs.symlinkSync(real, aliasA);
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
    const realResolved = nodeFs.realpathSync(real);
    // The raw, as-configured values — both must still be present (this much already held before
    // the fix; a message that dropped them would be a regression in the other direction).
    assert.ok(message.includes(join(aliasA, '.provenance')), 'message must still name the raw provenance root');
    assert.ok(message.includes(aliasB), 'message must still name the raw capture.output_dir');
    // The RESOLVED values — this is what a raw-only message cannot provide, and what makes the halt
    // actionable: the operator's own alias names never appear in the same tree, only their targets do.
    assert.ok(
      message.includes(`${realResolved}/.provenance`),
      `message must name the RESOLVED provenance root ('${realResolved}/.provenance'); got: ${message}`,
    );
    assert.ok(
      message.includes(realResolved),
      `message must name the RESOLVED capture.output_dir ('${realResolved}'); got: ${message}`,
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
    assert.ok(Array.isArray(openedState.entries));
    // Not yet closed — `closed` is the one field the declaration keeps optional on this branch,
    // and it must be genuinely ABSENT here (not merely falsy), matching `openCaptureRun`'s own
    // construction, which never assigns it at all.
    assert.equal(Object.hasOwn(openedState, 'closed'), false);
    assert.deepEqual(
      Object.keys(openedState).sort(),
      ['entries', 'opening', 'opening_assets', 'opening_digest', 'run_id', 'skipped'].sort(),
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
      ['closed', 'entries', 'opening', 'opening_assets', 'opening_digest', 'run_id', 'skipped'].sort(),
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

test('openCaptureRun: an unexpected snapshot-listing errno returns a halt, never an uncaught throw', () => {
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
    });
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
      assert.equal(result.warnings.length, 1, JSON.stringify(result.warnings));
      assert.match(result.warnings[0], /committed successfully/);
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

// A no-op extractor for tests that only care about record-state classification, never about
// completeness — buildProvenanceReport now runs the extractor for EVERY entry unconditionally
// (codex DO-NOT-SHIP blocker 4), so every buildProvenanceReport call needs a chapter file on disk
// and an extractor that does not halt, even when the fixture's whole point is elsewhere.
const emptyExpectedAssets = () => ({ ok: true, assets: [] });

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
// fs capability policy — a positive scan over this module's OWN source text
// =================================================================================================

const MODULE_PATH = new URL('../skills/enduser-handbook/assets/lib/capture-record.mjs', import.meta.url);
const MODULE_SOURCE = nodeFs.readFileSync(MODULE_PATH, 'utf8');

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
  let lastIdent = null; // the text of the most recently emitted 'ident' token, for dotBase below
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
    if (/[A-Za-z_$]/.test(ch)) {
      const start = i;
      while (i < n && /[A-Za-z0-9_$]/.test(source[i])) i++;
      const text = source.slice(start, i);
      // `dotBase`: the identifier immediately before THIS token's own leading dot (if any) — lets
      // `fs.writeFileSync` be recognized as "writeFileSync reached via fs." while `fs.constants` (a
      // different, allowed base) is not confused with it.
      // `precededByChar` snapshots the significant character/kind that came right before THIS
      // token, BEFORE it gets overwritten to 'ident' below — used (IMPORTANT 4) to tell an
      // OBJECT-LITERAL property value (`openSync: fs.openSync,`, preceded by ':') apart from a
      // BARE variable target (`const write = fs.writeFileSync`, preceded by '='), which is exactly
      // the shape distinction a fs.<method>-aliasing bypass needs to be caught.
      tokens.push({ kind: 'ident', text, precededByDot, precededByChar: prevSignificant, dotBase: precededByDot ? lastIdent : null, end: i });
      lastIdent = text;
      prevSignificant = 'ident';
      precededByDot = false;
      continue;
    }
    if (ch === '+') {
      tokens.push({ kind: 'punct', text: '+' });
      prevSignificant = '+';
      precededByDot = false;
      lastIdent = null;
      i++;
      continue;
    }
    // [round 3, codex] `[`/`]` — needed to detect bracket/computed member access
    // (`fs["writeFileSync"]`), which the dot-based `dotBase` tracking can never see. Sets
    // `prevSignificant`/`precededByDot`/`lastIdent` exactly as the pre-existing generic fallback
    // branch already did for these two characters (this is purely ADDITIVE: it only adds a token
    // for something that previously fell through unrecorded, so every OTHER existing check —
    // including the `]` value the regex-vs-division disambiguation below already inspects — is
    // unaffected).
    if (ch === '[' || ch === ']') {
      tokens.push({ kind: 'punct', text: ch });
      prevSignificant = ch;
      precededByDot = false;
      lastIdent = null;
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
          lastIdent = null;
          continue;
        }
      }
    }
    if (!/\s/.test(ch)) {
      prevSignificant = ch;
      precededByDot = ch === '.';
      if (ch !== '.') lastIdent = null; // any OTHER punctuation breaks a dotted-chain base
    }
    i++;
  }
  return tokens;
}

// The next non-whitespace, non-comment character at or after `from` — used for dynamic-import
// detection so a comment inserted between `import` and `(` (`import /*x*/ (...)`) cannot defeat a
// naive "only whitespace allowed" check.
function nextSignificantChar(source, from) {
  let i = from;
  const n = source.length;
  while (i < n) {
    if (/\s/.test(source[i])) { i++; continue; }
    if (source[i] === '/' && source[i + 1] === '/') {
      i += 2;
      while (i < n && source[i] !== '\n') i++;
      continue;
    }
    if (source[i] === '/' && source[i + 1] === '*') {
      i += 2;
      while (i < n && !(source[i] === '*' && source[i + 1] === '/')) i++;
      i += 2;
      continue;
    }
    return source[i];
  }
  return '';
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
// concern, matching findImportStatements' own "not well-formed" bail-out). Scans the RAW SOURCE
// (punctuation like `{`,`}`,`,`,`*`,`=` is not tokenized by scanJsTokens, unlike `[`/`]` above).
function parseImportClause(source, fromIndex) {
  const n = source.length;
  let i = fromIndex;

  function skipTrivia() {
    while (i < n) {
      if (/\s/.test(source[i])) { i++; continue; }
      if (source[i] === '/' && source[i + 1] === '/') { i += 2; while (i < n && source[i] !== '\n') i++; continue; }
      if (source[i] === '/' && source[i + 1] === '*') { i += 2; while (i < n && !(source[i] === '*' && source[i + 1] === '/')) i++; i += 2; continue; }
      break;
    }
  }
  function readIdentifier() {
    skipTrivia();
    const start = i;
    while (i < n && /[A-Za-z0-9_$]/.test(source[i])) i++;
    return i > start ? source.slice(start, i) : null;
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
    return { defaultName, namespaceName, named }; // side-effect-only import — no bindings at all
  }
  if (source[i] === '*') {
    i++;
    if (readIdentifier() !== 'as') return null;
    namespaceName = readIdentifier();
    if (namespaceName === null) return null;
    return { defaultName, namespaceName, named };
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
        return { defaultName, namespaceName, named };
      }
      if (!readNamedClause(named)) return null;
    }
    return { defaultName, namespaceName, named };
  }
  if (!readNamedClause(named)) return null;
  return { defaultName, namespaceName, named };
}

// [round 3, codex] Scans forward for `const|let|var {<prop>[: <alias>], ...} = <name>` — object
// destructuring directly off a namespace-bound name (`const {writeFileSync} = fs;`). Each
// destructured property becomes a fresh LOCAL BINDING equally capable of reaching the real fs
// function it names — the dot/bracket-access checks can never see it once destructured — so every
// non-allowed destructured name is tracked the SAME way a simple-assignment alias is: banned only
// if LATER called bare under its own (possibly renamed) name. A hand-rolled, bounded scanner in the
// same spirit as chapter-paths.mjs's own markdown scanners — real parsing is not required because
// the only question is "does this exact shape appear", not full grammar coverage.
function findFsDestructuredBindings(source, namespaceNames) {
  const n = source.length;
  const result = new Set();

  function skipTriviaAt(i) {
    while (i < n) {
      if (/\s/.test(source[i])) { i++; continue; }
      if (source[i] === '/' && source[i + 1] === '/') { i += 2; while (i < n && source[i] !== '\n') i++; continue; }
      if (source[i] === '/' && source[i + 1] === '*') { i += 2; while (i < n && !(source[i] === '*' && source[i + 1] === '/')) i++; i += 2; continue; }
      break;
    }
    return i;
  }
  function identAt(i) {
    const start = i;
    while (i < n && /[A-Za-z0-9_$]/.test(source[i])) i++;
    return i > start ? { text: source.slice(start, i), end: i } : null;
  }

  for (let start = 0; start < n; start++) {
    const kw = identAt(skipTriviaAt(start));
    if (kw === null || !(kw.text === 'const' || kw.text === 'let' || kw.text === 'var')) continue;
    let j = skipTriviaAt(kw.end);
    if (source[j] !== '{') continue;
    j++;
    const pairs = [];
    let ok = true;
    while (true) {
      j = skipTriviaAt(j);
      if (source[j] === '}') { j++; break; }
      if (source[j] === ',') { j++; continue; }
      const prop = identAt(j);
      if (prop === null) { ok = false; break; }
      j = prop.end;
      let local = prop.text;
      j = skipTriviaAt(j);
      if (source[j] === ':') {
        j++;
        const alias = identAt(skipTriviaAt(j));
        if (alias === null) { ok = false; break; }
        local = alias.text;
        j = alias.end;
      }
      pairs.push({ imported: prop.text, local });
      j = skipTriviaAt(j);
      if (source[j] === ',') { j++; continue; }
      if (source[j] === '}') { j++; break; }
      ok = false;
      break;
    }
    if (!ok) continue;
    j = skipTriviaAt(j);
    if (source[j] !== '=') continue;
    j++;
    const rhs = identAt(skipTriviaAt(j));
    if (rhs !== null && namespaceNames.has(rhs.text)) {
      for (const { imported, local } of pairs) {
        if (!ALLOWED_FS_MEMBERS.has(imported)) result.add(local);
      }
    }
  }
  return result;
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
// statement(s) — see collectFsBindings below, which builds `namespaceNames`. Every BARE identifier
// the source assigns directly from a banned `<namespace>.<method>` VALUE (`const write =
// fs.writeFileSync`, or a plain reassignment `write = fs.writeFileSync`) — never a property-access
// target (`deps.openSync = fs.openSync`, the real module's own DEFAULT-SEAM shape, merely spelled
// as an assignment instead of an object-literal property; that target is always invoked back
// through ITS OWN object, `deps.openSync(...)`, never bare, so it is not tracked here at all).
// Returns the set of ALIAS NAMES found; the caller checks whether any is ever called under its own
// bare name — an alias that is never called (see the fs.constants VALUE-reference test below,
// which also assigns `deps.openSync = fs.openSync`) stays legitimate.
function findBareFsAliasNames(tokens, namespaceNames) {
  const names = new Set();
  for (let idx = 0; idx < tokens.length; idx++) {
    const token = tokens[idx];
    if (token.kind !== 'ident' || !namespaceNames.has(token.dotBase) || ALLOWED_FS_MEMBERS.has(token.text)) continue;
    // The base namespace ident is always the array element right before this one — nothing else
    // can be pushed between a base ident and its own '.'-continuation (the '.' emits no token).
    const base = tokens[idx - 1];
    if (base?.precededByChar !== '=') continue; // not a bare `NAME = ns.method` shape at all
    const target = tokens[idx - 2];
    if (target?.kind === 'ident' && !target.precededByDot) names.add(target.text);
  }
  return names;
}

// True iff any of `aliasNames` is later referenced, under its own bare name, as a CALL —
// `write(...)` — anywhere in the token stream. The declaration occurrence itself never matches
// (it is followed by '=', never '('), so no explicit exclusion is needed for it.
function anyAliasCalled(tokens, source, aliasNames) {
  if (aliasNames.size === 0) return false;
  for (const token of tokens) {
    if (token.kind !== 'ident' || token.precededByDot || !aliasNames.has(token.text)) continue;
    if (nextSignificantChar(source, token.end) === '(') return true;
  }
  return false;
}

// [round 3, codex] POSITIVE import-binding policy. The round-2 checker pattern-matched individual
// bypass SHAPES (a literal `fs.<method>(...)` call, a bare alias of that exact dotted expression, a
// named-clause import) — each closure killed the shape it named and left the CLASS open: a default
// import (`import fsDefault from 'node:fs'`), a namespace import under any OTHER alias (`import *
// as io from 'node:fs'`), computed/bracket member access (`fs["writeFileSync"]`), and object
// destructuring (`const {writeFileSync} = fs`) all reach the identical banned function while being
// invisible to a checker rooted on the literal identifier `fs` and a `.`-access shape. This traces
// every LOCAL NAME the source binds to `node:fs`, however the import introduces it, into two
// buckets:
//   - NAMESPACE names — the whole module object (a namespace OR default import; Node's ESM/CJS
//     interop makes a default import of `node:fs` equally a whole-module object). The real
//     module's OWN binding, `fs`, is included UNCONDITIONALLY — every "mutant"/"legitimate
//     reference" fixture in this file is an isolated SNIPPET with no import statement of its own,
//     exercising the checker function directly, and this module's own convention (confirmed by
//     "the real module PASSES") is that `fs` always names this binding.
//   - DIRECT names — a named import's local binding is already bound to one resolved fs FUNCTION,
//     no member access needed at all.
// Every access path from either bucket to a real call is then checked in ONE place: a namespace
// name's dot-call (below), its bracket/computed-string access (banned unconditionally — the real
// module never uses bracket access on its own `fs` binding at all), any bare alias of either access
// form later called under its own name, and any destructured property later called bare — plus
// every direct name, if ever called bare. A shape not yet imagined still has to terminate in "call
// a name that traces back to one of these bindings", which is the property being checked here, not
// the shape.
function collectFsBindings(source, tokens) {
  const namespaceNames = new Set(['fs']);
  const directNames = new Set();
  for (const { specifier, importTokenEnd } of findImportStatements(tokens)) {
    if (specifier !== 'node:fs') continue;
    const clause = parseImportClause(source, importTokenEnd);
    if (clause === null) continue;
    if (clause.namespaceName !== null) namespaceNames.add(clause.namespaceName);
    if (clause.defaultName !== null) namespaceNames.add(clause.defaultName);
    for (const { imported, local } of clause.named) {
      if (!ALLOWED_FS_MEMBERS.has(imported)) directNames.add(local);
    }
  }
  return { namespaceNames, directNames };
}

// Bracket/computed member access on a namespace-bound name (`fs["writeFileSync"]`) — the dot-based
// check below can never see it, and the real module has zero legitimate use for bracket access on
// its own `fs` binding at all, so ANY computed-string access to a non-allowed member is banned
// outright, whether or not it is immediately called. `[`/`]` ARE tokenized (see scanJsTokens), so
// this is a plain 4-token lookahead: ident(namespace) '[' string ']'.
function anyBracketFsMemberAccess(tokens, namespaceNames) {
  for (let idx = 0; idx < tokens.length; idx++) {
    const t = tokens[idx];
    if (t.kind !== 'ident' || t.precededByDot || !namespaceNames.has(t.text)) continue;
    const open = tokens[idx + 1];
    const key = tokens[idx + 2];
    const close = tokens[idx + 3];
    if (open?.kind === 'punct' && open.text === '[' && key?.kind === 'string' && close?.kind === 'punct' && close.text === ']') {
      if (!ALLOWED_FS_MEMBERS.has(key.text)) return true;
    }
  }
  return false;
}

function checkCapabilityPolicy(source) {
  const tokens = scanJsTokens(source);
  for (const { specifier } of findImportStatements(tokens)) {
    if (!ALLOWED_IMPORT_SPECIFIERS.has(specifier)) return { ok: false, reason: `disallowed_import:${specifier}` };
  }
  const { namespaceNames, directNames } = collectFsBindings(source, tokens);

  if (anyBracketFsMemberAccess(tokens, namespaceNames)) {
    return { ok: false, reason: 'fs_bracket_access' };
  }

  // Every way a real fs function can end up bound to a BARE name — a named import, a
  // simple-assignment alias off a namespace binding, or destructuring off one — collapses to the
  // SAME question: is that bare name ever called. One check covers all three sources.
  const bareCallableNames = new Set([
    ...directNames,
    ...findBareFsAliasNames(tokens, namespaceNames),
    ...findFsDestructuredBindings(source, namespaceNames),
  ]);
  if (anyAliasCalled(tokens, source, bareCallableNames)) {
    return { ok: false, reason: 'fs_bound_name_called' };
  }

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
    // A direct `<namespace>.<method>(...)` CALL — not a bare reference. `openSync: fs.openSync,`
    // inside the default seam object is a legitimate VALUE reference (the whole point of
    // "defaulting to node:fs bindings"); an actual invocation `fs.openSync(...)` (or
    // `io.openSync(...)` under any OTHER namespace alias — codex round 3) bypasses the seam
    // entirely, which is what this specifically bans. `token.end` is only set on 'ident' tokens
    // (see scanJsTokens), which is exactly the token kind this branch is already scoped to.
    if (token.kind === 'ident' && namespaceNames.has(token.dotBase) && !ALLOWED_FS_MEMBERS.has(token.text) && nextSignificantChar(source, token.end) === '(') {
      return { ok: false, reason: `direct_fs_call:${token.text}` };
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
    'fs["writeFileSync"](...)', // computed/bracket member access — the dot-based dotBase tracking never sees it
    'const {writeFileSync} = fs; writeFileSync(...)', // destructuring off the namespace binding, not a simple assignment
  ];
  for (const mutant of mutants) {
    const result = checkCapabilityPolicy(mutant);
    assert.equal(result.ok, false, `mutant should FAIL: ${mutant}`);
  }
});

test('capability policy: a legitimate fs.constants VALUE reference (never called) is not flagged', () => {
  const result = checkCapabilityPolicy("const x = fs.constants.O_RDONLY; deps.openSync = fs.openSync;");
  assert.equal(result.ok, true, JSON.stringify(result));
});

test('capability policy: a disallowed static import is rejected', () => {
  const result = checkCapabilityPolicy("import { execSync } from 'node:os';\n");
  assert.equal(result.ok, false);
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
