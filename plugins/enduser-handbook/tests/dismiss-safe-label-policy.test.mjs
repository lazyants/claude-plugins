// Unit tests for the pure safe-negative-label gate used by dismissModal
// (capture-helpers.playwright.ts). Zero deps — runs under Node's built-in test runner:
// `node --test dismiss-safe-label-policy.test.mjs`.
//
// The EXACT-contents snapshots below catch an ADDITION to either list, not just a removal — a
// regression class a mere "every default admits" test would miss. The every-verb-via-safeLabels
// case is the core `||`→`&&` regression catcher: it proves a verb stays refused even when the
// exact label is added to safeLabels, so the leading-verb guard cannot be bypassed via the
// allowlist check alone.

import test from 'node:test';
import assert from 'node:assert/strict';

import {
  DEFAULT_SAFE_LABELS,
  UNSAFE_LEADING_VERBS,
  isSafeNegativeLabel,
} from '../skills/enduser-handbook/assets/lib/dismiss-safe-label-policy.mjs';

test('DEFAULT_SAFE_LABELS matches the exact verbatim EN+DE set (catches additions, not just removals)', () => {
  assert.deepEqual(DEFAULT_SAFE_LABELS, [
    'Cancel', 'No', 'Close', 'Back', 'Go back', 'Keep', 'Dismiss', 'Not now',
    'Abbrechen', 'Nein', 'Schließen', 'Zurück', 'Behalten',
  ]);
});

test('UNSAFE_LEADING_VERBS matches the exact verbatim EN+DE set (catches additions, not just removals)', () => {
  assert.deepEqual(UNSAFE_LEADING_VERBS, [
    'delete', 'destroy', 'remove', 'disable', 'deactivate', 'approve', 'send', 'finalize',
    'revoke', 'reset', 'purge', 'wipe', 'confirm', 'submit', 'save',
    'löschen', 'entfernen', 'bestätigen', 'senden', 'speichern',
  ]);
});

test('both exported arrays are frozen (immutable snapshots)', () => {
  assert.throws(() => DEFAULT_SAFE_LABELS.push('Proceed'));
  assert.throws(() => UNSAFE_LEADING_VERBS.push('proceed'));
});

test('every default safe label is admitted', () => {
  for (const label of DEFAULT_SAFE_LABELS) {
    assert.equal(isSafeNegativeLabel(label), true, `expected "${label}" to be safe`);
  }
});

test('every unsafe leading verb is refused even when the exact label is added to safeLabels (the ||→&& regression catcher)', () => {
  for (const verb of UNSAFE_LEADING_VERBS) {
    const label = `${verb} now`;
    assert.equal(
      isSafeNegativeLabel(label, [label]),
      false,
      `expected "${label}" to stay refused even via safeLabels`,
    );
  }
});

test('a label not on the allowlist is refused', () => {
  assert.equal(isSafeNegativeLabel('Delete'), false);
  assert.equal(isSafeNegativeLabel('Save changes'), false);
});

test('a destructive label is refused even though it contains a safe substring (exact match only)', () => {
  assert.equal(isSafeNegativeLabel('Cancel and delete'), false);
});

test('case and trailing-whitespace variants are refused — no normalization, exact match only', () => {
  assert.equal(isSafeNegativeLabel('cancel'), false);
  assert.equal(isSafeNegativeLabel('Cancel '), false);
});

test('safeLabels extends the allowlist with a project-specific safe negative', () => {
  assert.equal(isSafeNegativeLabel('Verwerfen', ['Verwerfen']), true);
});

test('an empty label is refused', () => {
  assert.equal(isSafeNegativeLabel(''), false);
});
