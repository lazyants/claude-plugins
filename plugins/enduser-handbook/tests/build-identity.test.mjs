// Unit tests for the build-identity/provenance pure module. Zero deps — runs under Node's built-in
// test runner: `node --test build-identity.test.mjs`.
//
// Special characters are built with String.fromCharCode/fromCodePoint rather than \u escapes in
// string/regex literals, so the exact code point under test is unambiguous in source and in diffs
// (matching profile-version.test.mjs's convention).

import test from 'node:test';
import assert from 'node:assert/strict';

import {
  RESOLUTION_REASONS,
  IDENTITY_SOURCES,
  UI_READ_REGION_HINT,
  normalizeBuildIdentity,
  sanitizeDetail,
  resolveBuildIdentity,
  resolveClosingIdentity,
  isValidBuildIdentityField,
  verifyRecord,
  classifyBuildDelta,
  formatIdentityValue,
  describeBuildIdentityWarning,
} from '../skills/enduser-handbook/assets/lib/build-identity.mjs';

// Unicode code points, built without \u escapes in source.
const NBSP = String.fromCharCode(0x00a0);
const BOM = String.fromCharCode(0xfeff);
const LS = String.fromCharCode(0x2028);
const PS = String.fromCharCode(0x2029);
const ASTRAL = String.fromCodePoint(0x1f600); // an astral (surrogate-pair) character
const LATIN1_E_ACUTE = String.fromCharCode(0xe9); // 'é'

// ---- exported constants -------------------------------------------------------------------------

test('exported constants', () => {
  assert.deepEqual(IDENTITY_SOURCES, ['command', 'ui', 'unavailable']);
  assert.equal(RESOLUTION_REASONS.length, 9);
  assert.deepEqual(new Set(RESOLUTION_REASONS), new Set([
    'no_source_configured',
    'command_failed',
    'command_output_rejected',
    'ui_read_unavailable',
    'ui_read_found_nothing',
    'ui_read_rejected',
    'build_changed_during_capture',
    'build_unconfirmed',
    'capture_failed',
  ]));
  assert.equal(typeof UI_READ_REGION_HINT, 'string');
  assert.ok(UI_READ_REGION_HINT.length > 0);
  // Frozen: a caller mutating the array must not corrupt the module's own copy.
  assert.throws(() => { RESOLUTION_REASONS.push('x'); });
  assert.throws(() => { IDENTITY_SOURCES.push('x'); });
});

// ==== normalizeBuildIdentity =======================================================================

test('normalizeBuildIdentity: accepts the named real-world shapes', () => {
  for (const v of [
    '4.3.1',
    'v1.2.3-rc.2',
    '1.2.3+build.4',
    'a'.repeat(40), // a 40-char SHA-shaped value
    '2024.10.1/abc123',
    '1!2.0', // PEP 440 epoch
    'none',
    'n/a',
    'unknown',
  ]) {
    const r = normalizeBuildIdentity(v);
    assert.equal(r.ok, true, `expected ${JSON.stringify(v)} to be accepted`);
    assert.equal(r.value, v);
  }
});

test('normalizeBuildIdentity: 128-char value accepted, 129-char rejected', () => {
  const v128 = 'a'.repeat(128);
  const v129 = 'a'.repeat(129);
  assert.equal(normalizeBuildIdentity(v128).ok, true);
  assert.equal(normalizeBuildIdentity(v129).ok, false);
});

test('normalizeBuildIdentity: one acceptance case per allowed non-alnum character class member', () => {
  // '.', '_', '+', ':', '~', '!', '/', '-' — every symbol the interior/trailing class admits, plus
  // ':' and '~' called out explicitly since they are the least obviously "version-shaped".
  const v = 'A0._+:~!/-Z';
  const r = normalizeBuildIdentity(v);
  assert.equal(r.ok, true);
  assert.equal(r.value, v);
});

test('normalizeBuildIdentity: closed by sweep — every disallowed ASCII codepoint rejected at leading/interior/trailing position', () => {
  const ALLOWED = new Set('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._+:~!/-'.split(''));
  // Space (0x20) and tab (0x09) are deliberately EXEMPT from both the leading and trailing checks:
  // they are stripped as boundary padding before the grammar test ever runs (see "quote unwrapping
  // and space/tab padding" above), so a leading/trailing space or tab legitimately survives into an
  // otherwise-valid value. '\n' and '\r' are exempt from the TRAILING check only — "at most one
  // trailing line terminator" is stripped, so a single trailing one survives; a LEADING one is
  // never stripped and must still reject. None of the five is exempt from the INTERIOR check: an
  // interior space, tab, or line terminator is never stripped and must still be rejected.
  const BOUNDARY_STRIPPED = new Set([' ', '\t']);
  const TRAILING_STRIPPED = new Set([' ', '\t', '\n', '\r']);
  for (let code = 0; code <= 127; code += 1) {
    const ch = String.fromCharCode(code);
    if (ALLOWED.has(ch)) continue;
    if (!BOUNDARY_STRIPPED.has(ch)) {
      assert.equal(normalizeBuildIdentity(`${ch}ab`).ok, false, `leading ${code} should reject`);
    }
    if (!TRAILING_STRIPPED.has(ch)) {
      assert.equal(normalizeBuildIdentity(`ab${ch}`).ok, false, `trailing ${code} should reject`);
    }
    assert.equal(normalizeBuildIdentity(`a${ch}b`).ok, false, `interior ${code} should reject`);
  }
});

test('normalizeBuildIdentity: closed by sweep — non-ASCII samples rejected at leading/interior/trailing, incl NBSP (not stripped by a real trim)', () => {
  // NBSP is the case that catches a JS `.trim()` (which strips NBSP) standing in for the required
  // ASCII-space-and-tab-only trim: if a bad trim silently removed a boundary NBSP, only the interior
  // case would still fail, and the leading/trailing cases would wrongly pass.
  for (const ch of [NBSP, BOM, LS, PS, LATIN1_E_ACUTE, ASTRAL]) {
    assert.equal(normalizeBuildIdentity(`${ch}ab`).ok, false, `leading ${JSON.stringify(ch)} should reject`);
    assert.equal(normalizeBuildIdentity(`a${ch}b`).ok, false, `interior ${JSON.stringify(ch)} should reject`);
    assert.equal(normalizeBuildIdentity(`ab${ch}`).ok, false, `trailing ${JSON.stringify(ch)} should reject`);
  }
});

test('normalizeBuildIdentity: exactly one trailing line terminator stripped, a second left behind fails', () => {
  assert.equal(normalizeBuildIdentity('4.3.1\n').ok, true);
  assert.deepEqual(normalizeBuildIdentity('4.3.1\n'), { ok: true, value: '4.3.1' });
  assert.equal(normalizeBuildIdentity('4.3.1\r\n').ok, true);
  assert.equal(normalizeBuildIdentity('4.3.1\r').ok, true);
  assert.equal(normalizeBuildIdentity('4.3.1\n\n').ok, false);
  assert.equal(normalizeBuildIdentity('4.3.1\r\n\r\n').ok, false);
});

test('normalizeBuildIdentity: quote unwrapping and space/tab padding', () => {
  assert.deepEqual(normalizeBuildIdentity('"4.3.1"\n'), { ok: true, value: '4.3.1' });
  assert.deepEqual(normalizeBuildIdentity('  4.3.1  '), { ok: true, value: '4.3.1' });
  assert.deepEqual(normalizeBuildIdentity('\t4.3.1\t'), { ok: true, value: '4.3.1' });
});

test('normalizeBuildIdentity: an UNMATCHED leading or trailing quote is rejected, not tolerated', () => {
  assert.equal(normalizeBuildIdentity('"4.3.1').ok, false);
  assert.equal(normalizeBuildIdentity('4.3.1"').ok, false);
});

test('normalizeBuildIdentity: rejects empty, whitespace-only, leading punctuation, interior space', () => {
  assert.equal(normalizeBuildIdentity('').ok, false);
  assert.equal(normalizeBuildIdentity('   ').ok, false);
  assert.equal(normalizeBuildIdentity('.v1').ok, false);
  assert.equal(normalizeBuildIdentity('1.2 3').ok, false);
});

test('normalizeBuildIdentity: a non-string raw is rejected, never coerced', () => {
  const r = normalizeBuildIdentity(431);
  assert.equal(r.ok, false);
  // Must NOT be the passing wrong normalizer `String(raw)` -> "431" (which would otherwise be a
  // grammar-valid value).
  assert.notEqual(r.value, '431');
});

test('normalizeBuildIdentity: COMPOSITE fixture — quote-unwrap + horizontal-whitespace trim + newline all interacting', () => {
  const composite = ' \t"4.3.1"\t \r\n';
  assert.deepEqual(normalizeBuildIdentity(composite), { ok: true, value: '4.3.1' });
});

// ==== sanitizeDetail ===============================================================================

test('sanitizeDetail: exhaustive sweep over every Unicode scalar value (minus surrogates)', () => {
  const SURROGATE_LOW = 0xd800;
  const SURROGATE_HIGH = 0xdfff;
  for (let cp = 0; cp <= 0x10ffff; cp += 1) {
    if (cp >= SURROGATE_LOW && cp <= SURROGATE_HIGH) continue;
    const ch = String.fromCodePoint(cp);
    const result = sanitizeDetail(ch);
    if (cp >= 0x20 && cp <= 0x7e) {
      assert.equal(result, ch, `printable ASCII/space codepoint ${cp} must survive`);
    } else {
      assert.equal(result, '', `non-printable codepoint ${cp} must be removed`);
    }
  }
});

test('sanitizeDetail: unpaired surrogate code units are removed', () => {
  const loneHigh = String.fromCharCode(0xd800);
  const loneLow = String.fromCharCode(0xdc00);
  assert.equal(sanitizeDetail(loneHigh), '');
  assert.equal(sanitizeDetail(loneLow), '');
});

test('sanitizeDetail: repeated and interleaved invalid codepoints are ALL removed, not just the first', () => {
  const nul = String.fromCharCode(0x0000);
  const soh = String.fromCharCode(0x0001);
  const stx = String.fromCharCode(0x0002);
  const etx = String.fromCharCode(0x0003);
  assert.equal(sanitizeDetail(`${nul}${soh}`), '');
  assert.equal(sanitizeDetail(`a${LATIN1_E_ACUTE}b${LS}c`), 'abc');
  // At least one fixture carries FOUR invalid codepoints, interleaved with good ones.
  assert.equal(sanitizeDetail(`a${nul}b${soh}c${stx}d${etx}e`), 'abcde');
});

test('sanitizeDetail: bounds to 200 chars including the "..." marker; a short detail is left unmarked', () => {
  const short = 'npm pkg get version';
  assert.equal(sanitizeDetail(short), short);

  const long = 'x'.repeat(250);
  const result = sanitizeDetail(long);
  assert.equal(result.length, 200);
  assert.ok(result.endsWith('...'));
  assert.equal(result, `${'x'.repeat(197)}...`);

  // Exactly at the bound: no marker.
  const exact = 'x'.repeat(200);
  assert.equal(sanitizeDetail(exact), exact);
  assert.equal(sanitizeDetail(exact).length, 200);
});

test('sanitizeDetail: invalid codepoints straddling the 200-char truncation boundary — removal runs to completion BEFORE truncation', () => {
  // 200 good chars with a bad codepoint inserted at position 195 (which would sit just inside a
  // truncate-then-sanitize implementation's cutoff) plus 10 more good chars after it. If removal
  // happened only up to the truncation point, the bad char's removal would shift what makes the
  // final 200-char cut; sanitize-then-truncate makes the cut deterministic regardless.
  const nul = String.fromCharCode(0x0000);
  const raw = `${'a'.repeat(195)}${nul}${'b'.repeat(20)}`;
  const result = sanitizeDetail(raw);
  // 195 'a' + 20 'b' = 215 clean chars, truncated to 200 with a marker.
  assert.equal(result.length, 200);
  assert.ok(result.endsWith('...'));
  assert.equal(result, `${'a'.repeat(195)}${'b'.repeat(2)}...`);
});

test('sanitizeDetail: a non-string input is coerced defensively rather than throwing', () => {
  assert.equal(sanitizeDetail(431), '431');
});

// [round 14] The test above says "rather than throwing" and exercises a number, which cannot throw
// — so the claim in its own name went unchecked. These are the inputs where `String(x)` actually
// throws: a null-prototype object has no `Symbol.toPrimitive`, no `toString` and no `valueOf`, and
// an object whose `toString` throws is the same case reached deliberately. The module header states
// that exactly two functions throw on out-of-domain input; this was a silent third.
test('sanitizeDetail: an UNSTRINGIFIABLE input degrades to a marker rather than throwing', () => {
  for (const raw of [Object.create(null), { toString() { throw new Error('nope'); } }, Object.assign(Object.create(null), { a: 1 })]) {
    const result = sanitizeDetail(raw);
    assert.equal(typeof result, 'string');
    assert.ok(result.length > 0, 'a detail must still say something about what was seen');
  }
});

// ==== resolveBuildIdentity =========================================================================

test('resolveBuildIdentity: a valid command performs ZERO ui reads, source "command"', () => {
  // No uiObservation supplied at all — if this ever reached the UI branch it would return
  // needs_ui_read instead of a resolved value, so a resolved 'command' value proves the UI branch
  // was never taken.
  const r = resolveBuildIdentity({ commandOutcome: { ok: true, raw: '4.3.1' } });
  assert.deepEqual(r, { value: '4.3.1', source: 'command', resolution_reason: null, detail: null });
});

test('resolveBuildIdentity: command detail is sanitized and carried through', () => {
  const r = resolveBuildIdentity({ commandOutcome: { ok: true, raw: '4.3.1', detail: 'npm pkg get version' } });
  assert.equal(r.detail, 'npm pkg get version');
});

test('resolveBuildIdentity: no command, ui_read false -> no_source_configured', () => {
  const r = resolveBuildIdentity({ commandOutcome: null, uiReadEnabled: false });
  assert.deepEqual(r, { value: null, source: 'unavailable', resolution_reason: 'no_source_configured', detail: null });
});

test('resolveBuildIdentity: command failed to run, ui_read false -> command_failed', () => {
  const r = resolveBuildIdentity({ commandOutcome: { ok: false }, uiReadEnabled: false });
  assert.equal(r.resolution_reason, 'command_failed');
  assert.equal(r.value, null);
  assert.equal(r.source, 'unavailable');
});

test('resolveBuildIdentity: command ran but output rejected, ui_read false -> command_output_rejected', () => {
  const r = resolveBuildIdentity({ commandOutcome: { ok: true, raw: '???' }, uiReadEnabled: false });
  assert.equal(r.resolution_reason, 'command_output_rejected');
});

test('resolveBuildIdentity: an omitted ui_read resolves to the UI-read path (opt-out default)', () => {
  const r = resolveBuildIdentity({ commandOutcome: null });
  assert.deepEqual(r, { needs_ui_read: true, region_hint: UI_READ_REGION_HINT });
});

test('resolveBuildIdentity: not_attempted (or absent) UI observation requests a read, whether or not a command was configured', () => {
  assert.deepEqual(resolveBuildIdentity({ commandOutcome: null, uiObservation: { kind: 'not_attempted' } }), {
    needs_ui_read: true,
    region_hint: UI_READ_REGION_HINT,
  });
  assert.deepEqual(resolveBuildIdentity({ commandOutcome: { ok: false }, uiObservation: undefined }), {
    needs_ui_read: true,
    region_hint: UI_READ_REGION_HINT,
  });
});

test('resolveBuildIdentity: command-fails-then-UI-succeeds records source "ui"', () => {
  const r = resolveBuildIdentity({
    commandOutcome: { ok: false },
    uiObservation: { kind: 'value', raw: '4.3.1' },
  });
  assert.deepEqual(r, { value: '4.3.1', source: 'ui', resolution_reason: null, detail: null });
});

test('resolveBuildIdentity: every UI terminal kind maps to its own resolution_reason', () => {
  assert.equal(
    resolveBuildIdentity({ uiObservation: { kind: 'found_nothing' } }).resolution_reason,
    'ui_read_found_nothing',
  );
  assert.equal(
    resolveBuildIdentity({ uiObservation: { kind: 'unavailable' } }).resolution_reason,
    'ui_read_unavailable',
  );
  assert.equal(
    resolveBuildIdentity({ uiObservation: { kind: 'rejected' } }).resolution_reason,
    'ui_read_rejected',
  );
});

test('resolveBuildIdentity: a value whose raw fails normalization is terminal ui_read_rejected, never needs_ui_read', () => {
  const r = resolveBuildIdentity({ uiObservation: { kind: 'value', raw: 'not a valid identity!!' } });
  assert.equal(r.resolution_reason, 'ui_read_rejected');
  assert.equal(r.value, null);
  assert.notEqual(r.needs_ui_read, true);
});

test('resolveBuildIdentity: a non-string raw at the UI entrypoint is its own mutant — rejected, never coerced', () => {
  const r = resolveBuildIdentity({ uiObservation: { kind: 'value', raw: 431 } });
  assert.equal(r.resolution_reason, 'ui_read_rejected');
  assert.notEqual(r.value, '431');
});

test('resolveBuildIdentity: an unrecognized uiObservation.kind fails closed (throws) rather than silently resolving', () => {
  // [round 7] The module header (build-identity.mjs:17-21) documents that exactly two functions
  // throw a TypeError on an out-of-domain input — this one and classifyBuildDelta (see that
  // function's own pinned-class assertions below). A class-blind assertion here survives a mutant
  // that swaps the TypeError for a plain Error, silently losing that documented distinction.
  assert.throws(() => resolveBuildIdentity({ uiObservation: { kind: 'bogus' } }), TypeError);
});

// ==== resolveClosingIdentity =======================================================================

const OPENING_COMMAND_A = { value: 'A', source: 'command', resolution_reason: null, detail: 'opening detail' };
const OPENING_UI_A = { value: 'A', source: 'ui', resolution_reason: null, detail: 'opening ui detail' };
const CLOSING_COMMAND_A = { value: 'A', source: 'command', resolution_reason: null, detail: 'closing detail' };
const CLOSING_UI_A = { value: 'A', source: 'ui', resolution_reason: null, detail: 'closing ui detail' };
const UNRESOLVED = (reason, detail = null) => ({ value: null, source: 'unavailable', resolution_reason: reason, detail });

test('resolveClosingIdentity: rule 0 — capture_failed outranks everything, even a clean opening/closing pair', () => {
  const r = resolveClosingIdentity({
    opening: OPENING_COMMAND_A,
    captureOutcome: { ok: false },
    closing: CLOSING_COMMAND_A,
  });
  assert.deepEqual(r, { value: null, source: 'unavailable', resolution_reason: 'capture_failed', detail: null });
});

test('resolveClosingIdentity: rule 0 outranks rule 1 too — capture_failed wins even when the OPENING itself already failed', () => {
  // The gap a "rule 0 outranks rule 1" claim can hide in: a test where only ONE of the two
  // conditions is ever true at once can pass under either check ORDER. This fixture makes both
  // true simultaneously, so a reordered/short-circuited implementation that lets a failed opening
  // report its own reason ahead of a failed capture would show up here, not just in the "clean
  // opening" fixture above.
  const opening = UNRESOLVED('command_failed', 'opening failure detail');
  const r = resolveClosingIdentity({ opening, captureOutcome: { ok: false }, closing: CLOSING_COMMAND_A });
  assert.deepEqual(r, { value: null, source: 'unavailable', resolution_reason: 'capture_failed', detail: null });
});

test('resolveClosingIdentity: rule 1 — a failed opening keeps its own reason and detail verbatim', () => {
  const opening = UNRESOLVED('command_failed', 'opening failure detail');
  const r = resolveClosingIdentity({ opening, captureOutcome: { ok: true }, closing: CLOSING_COMMAND_A });
  assert.deepEqual(r, { value: null, source: 'unavailable', resolution_reason: 'command_failed', detail: 'opening failure detail' });
});

test('resolveClosingIdentity: rule 2 — a closing failure after a successful opening is build_unconfirmed, with the closing reason in DETAIL, never resolution_reason', () => {
  const closing = UNRESOLVED('ui_read_unavailable');
  const r = resolveClosingIdentity({ opening: OPENING_COMMAND_A, captureOutcome: { ok: true }, closing });
  assert.equal(r.resolution_reason, 'build_unconfirmed');
  assert.notEqual(r.resolution_reason, 'ui_read_unavailable');
  assert.equal(r.detail, 'ui_read_unavailable');
  assert.equal(r.value, null);
  assert.equal(r.source, 'unavailable');
});

test('resolveClosingIdentity: rule 3 — different values -> build_changed_during_capture, source unavailable (not the weaker of the two)', () => {
  const closing = { value: 'B', source: 'command', resolution_reason: null, detail: null };
  const r = resolveClosingIdentity({ opening: OPENING_COMMAND_A, captureOutcome: { ok: true }, closing });
  assert.equal(r.resolution_reason, 'build_changed_during_capture');
  assert.equal(r.value, null);
  assert.equal(r.source, 'unavailable');
});

test('resolveClosingIdentity: rule 4 — equal values record the WEAKER source, in BOTH orders', () => {
  const orderA = resolveClosingIdentity({ opening: OPENING_COMMAND_A, captureOutcome: { ok: true }, closing: CLOSING_UI_A });
  assert.equal(orderA.value, 'A');
  assert.equal(orderA.source, 'ui');
  assert.equal(orderA.resolution_reason, null);

  const orderB = resolveClosingIdentity({ opening: OPENING_UI_A, captureOutcome: { ok: true }, closing: CLOSING_COMMAND_A });
  assert.equal(orderB.value, 'A');
  assert.equal(orderB.source, 'ui');
  assert.equal(orderB.resolution_reason, null);
});

test('resolveClosingIdentity: equal values from the SAME source stay that source', () => {
  const r = resolveClosingIdentity({ opening: OPENING_COMMAND_A, captureOutcome: { ok: true }, closing: CLOSING_COMMAND_A });
  assert.equal(r.source, 'command');
  assert.equal(r.value, 'A');
});

// ==== isValidBuildIdentityField ====================================================================

const VALID_COMMAND_FIELD = { value: '4.3.1', source: 'command', resolution_reason: null, detail: 'npm pkg get version' };
const VALID_NULL_FIELD = { value: null, source: 'unavailable', resolution_reason: 'no_source_configured', detail: null };

test('isValidBuildIdentityField: accepts the two canonical shapes (known value, null value)', () => {
  assert.deepEqual(isValidBuildIdentityField(VALID_COMMAND_FIELD), { ok: true });
  assert.deepEqual(isValidBuildIdentityField(VALID_NULL_FIELD), { ok: true });
});

test('isValidBuildIdentityField: detail is REQUIRED — every constructor in this module (resolveBuildIdentity, resolveClosingIdentity) always assigns it, so an absent detail never comes from a value this module produced', () => {
  const { detail: _drop, ...withoutDetail } = VALID_COMMAND_FIELD;
  assert.deepEqual(isValidBuildIdentityField(withoutDetail), { ok: false, reason: 'detail_missing' });
  assert.deepEqual(isValidBuildIdentityField({ ...VALID_COMMAND_FIELD, detail: undefined }), {
    ok: false,
    reason: 'detail_missing',
  });
});

test('isValidBuildIdentityField: rejects a non-object, an array, and null', () => {
  assert.equal(isValidBuildIdentityField(null).ok, false);
  assert.equal(isValidBuildIdentityField('x').ok, false);
  assert.equal(isValidBuildIdentityField([]).ok, false);
  assert.equal(isValidBuildIdentityField(42).ok, false);
});

test('isValidBuildIdentityField: value neither string nor null', () => {
  assert.equal(isValidBuildIdentityField({ ...VALID_COMMAND_FIELD, value: 42 }).ok, false);
});

test('isValidBuildIdentityField: value not already canonical (untrimmed/quoted) is rejected at read, not silently re-normalized', () => {
  assert.equal(isValidBuildIdentityField({ ...VALID_COMMAND_FIELD, value: ' 4.3.1 ' }).ok, false);
  assert.equal(isValidBuildIdentityField({ ...VALID_COMMAND_FIELD, value: '"4.3.1"' }).ok, false);
  assert.equal(isValidBuildIdentityField({ ...VALID_COMMAND_FIELD, value: 'x'.repeat(300) }).ok, false);
  const nul = String.fromCharCode(0);
  assert.equal(isValidBuildIdentityField({ ...VALID_COMMAND_FIELD, value: `4.3.1${nul}` }).ok, false);
});

test('isValidBuildIdentityField: source outside the enum', () => {
  assert.equal(isValidBuildIdentityField({ ...VALID_COMMAND_FIELD, source: 'bogus' }).ok, false);
});

test('isValidBuildIdentityField: resolution_reason neither null nor a member of the enum', () => {
  assert.equal(isValidBuildIdentityField({ ...VALID_NULL_FIELD, resolution_reason: 'bogus' }).ok, false);
});

test('isValidBuildIdentityField: resolution_reason null while source is unavailable', () => {
  assert.equal(isValidBuildIdentityField({ ...VALID_NULL_FIELD, resolution_reason: null }).ok, false);
});

test('isValidBuildIdentityField: resolution_reason non-null while a value was obtained', () => {
  assert.equal(isValidBuildIdentityField({ ...VALID_COMMAND_FIELD, resolution_reason: 'command_failed' }).ok, false);
});

test('isValidBuildIdentityField: value/source disagreement in EITHER direction', () => {
  assert.equal(isValidBuildIdentityField({ ...VALID_COMMAND_FIELD, source: 'unavailable' }).ok, false);
  assert.equal(isValidBuildIdentityField({ ...VALID_NULL_FIELD, source: 'command' }).ok, false);
});

test('isValidBuildIdentityField: detail present and not a string, or not already sanitized', () => {
  assert.equal(isValidBuildIdentityField({ ...VALID_COMMAND_FIELD, detail: {} }).ok, false);
  const nul = String.fromCharCode(0);
  assert.equal(isValidBuildIdentityField({ ...VALID_COMMAND_FIELD, detail: `has a ${nul} control char` }).ok, false);
  assert.equal(isValidBuildIdentityField({ ...VALID_COMMAND_FIELD, detail: 'x'.repeat(250) }).ok, false);
});

// ==== verifyRecord =================================================================================

test('verifyRecord: ok when current and recorded hashes match exactly', () => {
  const hashes = { 'overview.png': 'sha256:aaa', 'detail.png': 'sha256:bbb' };
  assert.deepEqual(verifyRecord(hashes, hashes), { status: 'ok' });
});

test('verifyRecord: ok when the record holds an EXTRA entry the chapter no longer embeds', () => {
  const recorded = { 'overview.png': 'sha256:aaa', 'removed.png': 'sha256:zzz' };
  const current = { 'overview.png': 'sha256:aaa' };
  assert.deepEqual(verifyRecord(recorded, current), { status: 'ok' });
});

test('verifyRecord: stale on a changed hash', () => {
  const recorded = { 'overview.png': 'sha256:aaa' };
  const current = { 'overview.png': 'sha256:changed' };
  const r = verifyRecord(recorded, current);
  assert.equal(r.status, 'stale');
  assert.equal(r.reason, 'embed_hash_changed');
});

test('verifyRecord: stale when a current embed is missing from the record entirely', () => {
  const recorded = { 'overview.png': 'sha256:aaa' };
  const current = { 'overview.png': 'sha256:aaa', 'new.png': 'sha256:new' };
  const r = verifyRecord(recorded, current);
  assert.equal(r.status, 'stale');
  assert.equal(r.reason, 'embed_missing_from_record');
});

test('verifyRecord: zero current embeds is NEVER ok, even with a non-empty recorded map', () => {
  const recorded = { 'overview.png': 'sha256:aaa' };
  const r = verifyRecord(recorded, {});
  assert.equal(r.status, 'stale');
  assert.equal(r.reason, 'no_current_embeds');
});

test('verifyRecord: compares EVERY current embed — a two-embed mixed fixture where only the SECOND is stale is still caught', () => {
  // A checker that stops after the first (clean) embed would wrongly report 'ok' in both cases.
  const recordedForMutated = { 'a.png': 'sha256:aaa', 'b.png': 'sha256:bbb' };
  const currentForMutated = { 'a.png': 'sha256:aaa', 'b.png': 'sha256:MUTATED' };
  const r1 = verifyRecord(recordedForMutated, currentForMutated);
  assert.equal(r1.status, 'stale');
  assert.equal(r1.reason, 'embed_hash_changed');
  assert.equal(r1.path, 'b.png');

  const recordedMissingSecond = { 'a.png': 'sha256:aaa' }; // b.png never made it into the record
  const currentBoth = { 'a.png': 'sha256:aaa', 'b.png': 'sha256:bbb' };
  const r2 = verifyRecord(recordedMissingSecond, currentBoth);
  assert.equal(r2.status, 'stale');
  assert.equal(r2.reason, 'embed_missing_from_record');
  assert.equal(r2.path, 'b.png');
});

test('verifyRecord: stable across a directory move — only relative keys are ever compared, no absolute root is consulted', () => {
  // verifyRecord never receives a root path at all; the same relative keys verify identically
  // however the caller derived them, which is exactly what makes an intact directory move a no-op.
  const beforeMove = { 'sub/overview.png': 'sha256:aaa' };
  const afterMove = { 'sub/overview.png': 'sha256:aaa' };
  assert.deepEqual(verifyRecord(beforeMove, afterMove), { status: 'ok' });
});

test('verifyRecord: a stray non-embedded file never enters the comparison (caller\'s contract, not verifyRecord\'s)', () => {
  // The caller is responsible for excluding non-embedded files from currentHashes; verifyRecord only
  // ever sees the sets it is handed, so an unrelated key sitting only in recordedHashes behaves
  // exactly like the "extra entry" case above.
  const recorded = { 'overview.png': 'sha256:aaa', 'current.json.deadbeef.tmp': 'sha256:irrelevant' };
  const current = { 'overview.png': 'sha256:aaa' };
  assert.deepEqual(verifyRecord(recorded, current), { status: 'ok' });
});

// ==== classifyBuildDelta ===========================================================================

const CURRENT_KNOWN_COMMAND = { value: '4.3.1', source: 'command', resolution_reason: null, detail: null };
const CURRENT_KNOWN_UI = { value: '4.3.1', source: 'ui', resolution_reason: null, detail: null };
const CURRENT_UNRESOLVED = { value: null, source: 'unavailable', resolution_reason: 'ui_read_unavailable', detail: null };
const RECORD_KNOWN_COMMAND = { value: '4.3.1', source: 'command', resolution_reason: null, detail: null };
const RECORD_KNOWN_DIFFERENT = { value: '4.2.0', source: 'command', resolution_reason: null, detail: null };
const RECORD_NULL = { value: null, source: 'unavailable', resolution_reason: 'no_source_configured', detail: null };

test('classifyBuildDelta: equal known values -> unchanged; unequal -> changed', () => {
  assert.deepEqual(
    classifyBuildDelta({ current: CURRENT_KNOWN_COMMAND, recordState: 'ok', record: RECORD_KNOWN_COMMAND }),
    { classification: 'unchanged', classification_reason: null, current_source: 'command', recorded_source: 'command' },
  );
  assert.deepEqual(
    classifyBuildDelta({ current: CURRENT_KNOWN_COMMAND, recordState: 'ok', record: RECORD_KNOWN_DIFFERENT }),
    { classification: 'changed', classification_reason: null, current_source: 'command', recorded_source: 'command' },
  );
});

test('classifyBuildDelta: absent, malformed and unsupported_version are indeterminate with DISTINCT reasons and a null recorded_source', () => {
  const absent = classifyBuildDelta({ current: CURRENT_KNOWN_COMMAND, recordState: 'absent', record: null });
  const malformed = classifyBuildDelta({ current: CURRENT_KNOWN_COMMAND, recordState: 'malformed', record: null });
  const unsupported = classifyBuildDelta({ current: CURRENT_KNOWN_COMMAND, recordState: 'unsupported_version', record: null });
  assert.equal(absent.classification, 'indeterminate');
  assert.equal(absent.classification_reason, 'record_absent');
  assert.equal(malformed.classification_reason, 'record_malformed');
  assert.equal(unsupported.classification_reason, 'record_unsupported_version');
  assert.notEqual(absent.classification_reason, malformed.classification_reason);
  assert.equal(absent.recorded_source, null);
  assert.equal(malformed.recorded_source, null);
  assert.equal(unsupported.recorded_source, null);
});

test('classifyBuildDelta: stale -> record_stale for BOTH an equal and an unequal value', () => {
  const staleEqual = classifyBuildDelta({ current: CURRENT_KNOWN_COMMAND, recordState: 'stale', record: RECORD_KNOWN_COMMAND });
  const staleDifferent = classifyBuildDelta({ current: CURRENT_KNOWN_COMMAND, recordState: 'stale', record: RECORD_KNOWN_DIFFERENT });
  assert.equal(staleEqual.classification, 'indeterminate');
  assert.equal(staleEqual.classification_reason, 'record_stale');
  assert.equal(staleDifferent.classification, 'indeterminate');
  assert.equal(staleDifferent.classification_reason, 'record_stale');
  // stale still reports a real recorded_source, unlike absent/malformed/unsupported.
  assert.equal(staleEqual.recorded_source, 'command');
});

test('classifyBuildDelta: a clean record with a null stored value is its OWN axis, not a record-state failure — reason is the record\'s own stored reason', () => {
  const r = classifyBuildDelta({ current: CURRENT_KNOWN_COMMAND, recordState: 'ok', record: RECORD_NULL });
  assert.equal(r.classification, 'indeterminate');
  assert.equal(r.classification_reason, 'no_source_configured');
  assert.equal(r.recorded_source, 'unavailable');
});

test('classifyBuildDelta: when BOTH sides are null, the record\'s own stored reason still wins', () => {
  const recordNullOtherReason = { value: null, source: 'unavailable', resolution_reason: 'command_failed', detail: null };
  const r = classifyBuildDelta({ current: CURRENT_UNRESOLVED, recordState: 'ok', record: recordNullOtherReason });
  assert.equal(r.classification, 'indeterminate');
  assert.equal(r.classification_reason, 'command_failed'); // the record's reason, not current's ui_read_unavailable
});

test('classifyBuildDelta: a clean KNOWN record with an unresolved current side reports the CURRENT side\'s own reason', () => {
  const r = classifyBuildDelta({ current: CURRENT_UNRESOLVED, recordState: 'ok', record: RECORD_KNOWN_COMMAND });
  assert.equal(r.classification, 'indeterminate');
  assert.equal(r.classification_reason, 'ui_read_unavailable');
});

test('classifyBuildDelta: both sources appear on EVERY verdict with their EXACT values — the distinctness cell is not satisfiable by a constant pair', () => {
  // record command vs current ui: a classifier returning a fixed command/command (or any constant)
  // pair fails this specific cell.
  const r = classifyBuildDelta({ current: CURRENT_KNOWN_UI, recordState: 'ok', record: RECORD_KNOWN_COMMAND });
  assert.equal(r.current_source, 'ui');
  assert.equal(r.recorded_source, 'command');
  assert.notEqual(r.current_source, r.recorded_source);
});

test('classifyBuildDelta: the clean-null x current-null cell legitimately reports unavailable/unavailable', () => {
  const r = classifyBuildDelta({ current: CURRENT_UNRESOLVED, recordState: 'ok', record: RECORD_NULL });
  assert.equal(r.current_source, 'unavailable');
  assert.equal(r.recorded_source, 'unavailable');
});

test('classifyBuildDelta: a non-BuildIdentity-shaped record (e.g. the whole chapter-record wrapper) throws rather than silently misclassifying', () => {
  // Measured regression case: a caller passing the CHAPTER RECORD wrapper — {record_version,
  // run_id, build_identity, asset_hashes} — instead of record.build_identity gets record.value
  // === undefined (not null), which used to fall through the null-value branch and compare
  // current.value === undefined, silently landing on 'changed' — a confidently WRONG verdict, not
  // an error. Both 'ok' and 'stale' must reject this shape identically, since both read `record`.
  const wrongShapeRecord = {
    record_version: 1,
    run_id: 'abc',
    build_identity: RECORD_KNOWN_COMMAND,
    asset_hashes: {},
  };
  assert.throws(
    () => classifyBuildDelta({ current: CURRENT_KNOWN_COMMAND, recordState: 'ok', record: wrongShapeRecord }),
    TypeError,
  );
  assert.throws(
    () => classifyBuildDelta({ current: CURRENT_KNOWN_COMMAND, recordState: 'stale', record: wrongShapeRecord }),
    TypeError,
  );
  // A record that IS the correct shape must still classify normally — this guard must not reject
  // valid input.
  assert.doesNotThrow(() =>
    classifyBuildDelta({ current: CURRENT_KNOWN_COMMAND, recordState: 'ok', record: RECORD_KNOWN_COMMAND }),
  );
});

// ==== formatIdentityValue ==========================================================================

test('formatIdentityValue: null renders as "unknown"; a real value renders as itself', () => {
  assert.equal(formatIdentityValue(null), 'unknown');
  assert.equal(formatIdentityValue('4.3.1'), '4.3.1');
  assert.equal(formatIdentityValue('unknown'), 'unknown'); // the STRING "unknown" is a legal identity value too
});

test('formatIdentityValue: a real resolved "unknown" and a genuinely absent value render the SAME word — only the adjacent `source` field tells them apart', () => {
  const realUnknown = resolveBuildIdentity({ commandOutcome: { ok: true, raw: 'unknown' } });
  const absent = resolveBuildIdentity({ commandOutcome: null, uiReadEnabled: false });
  // Same rendered word...
  assert.equal(formatIdentityValue(realUnknown.value), 'unknown');
  assert.equal(formatIdentityValue(absent.value), 'unknown');
  // ...but distinguishable by source, per this function's docstring.
  assert.equal(realUnknown.source, 'command');
  assert.equal(absent.source, 'unavailable');
  assert.notEqual(realUnknown.source, absent.source);
});

// ==== describeBuildIdentityWarning ==================================================================
// The W2 operator-facing warning line — closeCaptureRun (capture-record.mjs) reads this into its
// `warnings` array for a missing, failing, unconfirmed or changed FINAL identity (SKILL.md's own
// "W2 warns... on any of these outcomes"). Finding 2, this release: before this function existed,
// nothing rendered any of that into a warning a real run's caller ever saw.

test('describeBuildIdentityWarning: a clean resolution (resolution_reason null) warns about nothing', () => {
  const final = { value: 'A', source: 'command', resolution_reason: null, detail: null };
  assert.equal(describeBuildIdentityWarning({ opening: OPENING_COMMAND_A, closing: CLOSING_COMMAND_A, final }), null);
});

test('describeBuildIdentityWarning: build_changed_during_capture NAMES BOTH VALUES — SKILL.md\'s own contract for this outcome', () => {
  const opening = { value: 'v1', source: 'command', resolution_reason: null, detail: null };
  const closing = { value: 'v2', source: 'command', resolution_reason: null, detail: null };
  const final = resolveClosingIdentity({ opening, captureOutcome: { ok: true }, closing });
  assert.equal(final.resolution_reason, 'build_changed_during_capture');
  const warning = describeBuildIdentityWarning({ opening, closing, final });
  assert.match(warning, /v1/);
  assert.match(warning, /v2/);
});

test('describeBuildIdentityWarning: build_unconfirmed names the CLOSING side\'s own reason and detail, not just the bare word "build_unconfirmed"', () => {
  const closing = UNRESOLVED('command_failed', 'exit code 1');
  const final = resolveClosingIdentity({ opening: OPENING_COMMAND_A, captureOutcome: { ok: true }, closing });
  assert.equal(final.resolution_reason, 'build_unconfirmed');
  const warning = describeBuildIdentityWarning({ opening: OPENING_COMMAND_A, closing, final });
  assert.match(warning, /reconfirmed at close/);
  assert.match(warning, /failed to run/); // the command_failed clause, not the bare enum name
  assert.match(warning, /exit code 1/);
});

test('describeBuildIdentityWarning: capture_failed reads captureOutcome.detail (sanitized), since final.detail is always null on this branch', () => {
  const captureOutcome = { ok: false, detail: `boom${String.fromCharCode(0x00)}!` }; // an embedded NUL
  const final = resolveClosingIdentity({ opening: OPENING_COMMAND_A, captureOutcome, closing: CLOSING_COMMAND_A });
  assert.equal(final.resolution_reason, 'capture_failed');
  assert.equal(final.detail, null); // confirms this case has nothing of its own to fall back on
  const warning = describeBuildIdentityWarning({ opening: OPENING_COMMAND_A, closing: CLOSING_COMMAND_A, final, captureOutcome });
  assert.match(warning, /capture\.command itself failed/);
  assert.match(warning, /boom!/); // sanitized: the NUL byte is gone, the rest survives
});

test('describeBuildIdentityWarning: capture_failed with no captureOutcome.detail at all still renders a self-contained sentence', () => {
  const captureOutcome = { ok: false };
  const final = resolveClosingIdentity({ opening: OPENING_COMMAND_A, captureOutcome, closing: CLOSING_COMMAND_A });
  const warning = describeBuildIdentityWarning({ opening: OPENING_COMMAND_A, closing: CLOSING_COMMAND_A, final, captureOutcome });
  assert.match(warning, /capture\.command itself failed/);
});

test('describeBuildIdentityWarning: the six single-resolution reasons (rule 1 passthrough) each render a DISTINCT, non-generic clause, with the opening detail appended', () => {
  // opening.value === null forces resolveClosingIdentity's rule 1 — the opening's own reason and
  // detail carried straight through to `final`, verbatim — for every reason a lone
  // `resolveBuildIdentity` call can itself produce (RESOLUTION_REASONS minus the three composite
  // reasons only resolveClosingIdentity invents).
  const cases = [
    ['no_source_configured', /no build identity source is configured/],
    ['command_failed', /configured `capture\.build_identity\.command` failed to run/],
    ['command_output_rejected', /`capture\.build_identity\.command`'s output did not pass the identity grammar/],
    ['ui_read_unavailable', /a UI read of the build identity was unavailable/],
    ['ui_read_found_nothing', /a UI read of the build identity found nothing to read/],
    ['ui_read_rejected', /the UI-read build identity did not pass the identity grammar/],
  ];
  for (const [reason, clausePattern] of cases) {
    const opening = UNRESOLVED(reason, `detail for ${reason}`);
    const final = resolveClosingIdentity({ opening, captureOutcome: { ok: true }, closing: CLOSING_COMMAND_A });
    assert.equal(final.resolution_reason, reason);
    const warning = describeBuildIdentityWarning({ opening, closing: CLOSING_COMMAND_A, final });
    assert.match(warning, clausePattern, `reason '${reason}': got ${JSON.stringify(warning)}`);
    assert.match(warning, new RegExp(`detail for ${reason}`), `reason '${reason}' detail not appended: got ${JSON.stringify(warning)}`);
  }
});

test('describeBuildIdentityWarning: an unresolved opening with NO detail still renders a self-contained sentence (no trailing empty parenthesis)', () => {
  const opening = UNRESOLVED('no_source_configured', null);
  const final = resolveClosingIdentity({ opening, captureOutcome: { ok: true }, closing: CLOSING_COMMAND_A });
  const warning = describeBuildIdentityWarning({ opening, closing: CLOSING_COMMAND_A, final });
  assert.match(warning, /no build identity source is configured/);
  assert.doesNotMatch(warning, /\(\)/); // no empty "()" left over from an absent detail
});

test('reachability gate: every resolution_reason produces a NON-null, non-empty warning from describeBuildIdentityWarning (except null itself, which is the "no warning" case)', () => {
  const produced = new Map();

  const record = (opening, closing, captureOutcome = { ok: true }) => {
    const final = resolveClosingIdentity({ opening, captureOutcome, closing });
    const warning = describeBuildIdentityWarning({ opening, closing, final, captureOutcome });
    produced.set(final.resolution_reason, warning);
  };

  for (const reason of ['no_source_configured', 'command_failed', 'command_output_rejected', 'ui_read_unavailable', 'ui_read_found_nothing', 'ui_read_rejected']) {
    record(UNRESOLVED(reason), CLOSING_COMMAND_A);
  }
  record(OPENING_COMMAND_A, UNRESOLVED('command_failed'));       // build_unconfirmed
  record({ value: 'v1', source: 'command', resolution_reason: null, detail: null }, { value: 'v2', source: 'command', resolution_reason: null, detail: null }); // build_changed_during_capture
  record(OPENING_COMMAND_A, CLOSING_COMMAND_A, { ok: false });   // capture_failed

  const missing = RESOLUTION_REASONS.filter((reason) => !produced.has(reason) || !produced.get(reason));
  assert.deepEqual(missing, [], `resolution_reason members with no non-empty warning: ${missing.join(', ')}`);
});

// ==== reachability gate ============================================================================
// One test asserts every member of RESOLUTION_REASONS is produced by at least one fixture in this
// suite, and fails naming any member no fixture reaches (issue #110-style enum-reachability guard,
// applied here to resolution_reason).

test('reachability gate: every resolution_reason is produced by at least one fixture', () => {
  const produced = new Set();

  produced.add(resolveBuildIdentity({ commandOutcome: null, uiReadEnabled: false }).resolution_reason);
  produced.add(resolveBuildIdentity({ commandOutcome: { ok: false }, uiReadEnabled: false }).resolution_reason);
  produced.add(resolveBuildIdentity({ commandOutcome: { ok: true, raw: '???' }, uiReadEnabled: false }).resolution_reason);
  produced.add(resolveBuildIdentity({ uiObservation: { kind: 'unavailable' } }).resolution_reason);
  produced.add(resolveBuildIdentity({ uiObservation: { kind: 'found_nothing' } }).resolution_reason);
  produced.add(resolveBuildIdentity({ uiObservation: { kind: 'rejected' } }).resolution_reason);

  produced.add(
    resolveClosingIdentity({
      opening: OPENING_COMMAND_A,
      captureOutcome: { ok: true },
      closing: { value: 'B', source: 'command', resolution_reason: null, detail: null },
    }).resolution_reason,
  );
  produced.add(
    resolveClosingIdentity({
      opening: OPENING_COMMAND_A,
      captureOutcome: { ok: true },
      closing: UNRESOLVED('ui_read_unavailable'),
    }).resolution_reason,
  );
  produced.add(
    resolveClosingIdentity({
      opening: OPENING_COMMAND_A,
      captureOutcome: { ok: false },
      closing: CLOSING_COMMAND_A,
    }).resolution_reason,
  );

  const missing = RESOLUTION_REASONS.filter((reason) => !produced.has(reason));
  assert.deepEqual(missing, [], `resolution_reason members with no fixture in this suite: ${missing.join(', ')}`);
});
