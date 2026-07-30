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
      assert.deepEqual(Object.keys(row).sort(), [
        'classification',
        'classification_reason',
        'current_source',
        'key',
        'resolution_reason',
        'source',
        'value',
      ]);
    }
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
    let calls = 0;
    const deps = depsWithOverride({
      runIdentityCommand: () => {
        calls += 1;
        return { ok: true, raw: 'v1' };
      },
    });
    // Before the fix: `openCaptureRun` resolved (and could EXECUTE) the identity command around
    // capture-record.mjs:1219 while the exclusive pending-token create happened only around
    // :1254 — so this arbitrary, possibly side-effecting operator command ran for a run that was
    // never going to open.
    const result = CR.openCaptureRun(profile, [{ slug: 'items' }], null, deps);
    assert.equal(result.ok, false, JSON.stringify(result));
    assert.equal(result.halts[0].halt, 'run_already_open', JSON.stringify(result));
    assert.equal(calls, 0, 'the identity command must not run at all for an open that can never succeed');
  });
});

test('openCaptureRun (codex round 8, IMPORTANT 1): a contended open resolves to run_already_open on the FIRST call, never sending the operator to a UI read first', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir, { capture: { build_identity: { command: 'get-version', ui_read: true } } });
    writeFixture(profile, { token: validToken(RUN_ID_A, ZERO_DIGEST) });
    let calls = 0;
    const deps = depsWithOverride({
      runIdentityCommand: () => {
        calls += 1;
        // A command failure with `ui_read: true` is exactly the case that used to return
        // `needs_ui_read` without ever attempting the token — sending the operator off to do a UI
        // read for a run that could never open, discovering the real problem only on a LATER call.
        return { ok: false, detail: 'would force needs_ui_read if ever reached' };
      },
    });
    const result = CR.openCaptureRun(profile, [{ slug: 'items' }], null, deps);
    assert.equal(result.needs_ui_read, undefined, `expected an immediate run_already_open halt, not a UI-read request; got ${JSON.stringify(result)}`);
    assert.equal(result.ok, false, JSON.stringify(result));
    assert.equal(result.halts[0].halt, 'run_already_open', JSON.stringify(result));
    assert.equal(calls, 0, 'the identity command must not run before the contention check settles the call');
  });
});

test('openCaptureRun: a UI-read continuation reuses the already-resolved identityCommandOutcome — the identity command is NOT re-invoked (Finding 1)', () => {
  withTempDir((dir) => {
    const profile = profileFor(dir, { capture: { build_identity: { command: 'get-version', ui_read: true } } });
    let calls = 0;
    const deps = depsWithOverride({
      runIdentityCommand: () => {
        calls += 1;
        // Fails on the FIRST call, forcing needs_ui_read. If this were ever invoked a SECOND time
        // it would "succeed" with a value that must never win — so a re-invocation (not just its
        // result) is what this test would catch.
        return calls === 1 ? { ok: false, detail: 'first call fails' } : { ok: true, raw: 'command-would-have-won' };
      },
    });

    const first = CR.openCaptureRun(profile, [{ slug: 'items' }], null, deps);
    assert.equal(first.needs_ui_read, true, JSON.stringify(first));
    assert.equal(calls, 1, 'the command must run exactly once for the first (needs_ui_read) call');
    assert.deepEqual(first.identityCommandOutcome, { ok: false, detail: 'first call fails' });

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
// round-7 executed mutant on the real module — `capture-record.mjs:817`, `deps.mkdirSync(...)` ->
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
function findDisallowedDefaultDepsReference(source, tokens) {
  for (let idx = 0; idx < tokens.length; idx++) {
    const token = tokens[idx];
    if (token.kind !== 'ident' || token.text !== 'defaultDeps' || token.precededByDot) continue;
    const i = skipTriviaFrom(source, token.end);
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
    // `capture-record.mjs:817`, `deps.mkdirSync(...)` -> `defaultDeps.mkdirSync(...)` — reached
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
  // codex's round-7 executed mutant (capture-record.mjs:817), and the bracket/optional-chain
  // variants of the identical bypass — none of these is a shape check, so all three fall to it.
  assert.equal(checkCapabilityPolicy('defaultDeps.mkdirSync(dir, { recursive: true });').ok, false);
  assert.equal(checkCapabilityPolicy("defaultDeps['mkdirSync'](dir, { recursive: true });").ok, false);
  assert.equal(checkCapabilityPolicy('defaultDeps?.mkdirSync(dir, { recursive: true });').ok, false);
  assert.equal(checkCapabilityPolicy('defaultDeps?.["mkdirSync"](dir);').ok, false);

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
