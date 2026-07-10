// Unit tests for the per-role surface diff. Zero deps beyond control-inventory.mjs (for matrixLabel) —
// runs under Node's built-in test runner: `node --test surface-diff.test.mjs`.
//
// This is the REAL regression catcher for the per-role diff's headline contracts: the structural key
// excludes cosmetic label/class fields (so per-role labelling drift cannot fabricate a diff), the
// diff predicate 0-fills counts across the FULL declared role set (so membership asymmetry is caught,
// not just count asymmetry), and the documented equal-count directional-swap limitation stays an
// intentional, locked under-report rather than an accident.

import test from 'node:test';
import assert from 'node:assert/strict';

import { structuralKey, diffSurfaces } from '../skills/enduser-handbook/assets/lib/surface-diff.mjs';

// Minimal control-record builder. Only the fields a given test cares about need overriding; every
// other field defaults to null/false so a record always "looks like" a NormalizedControl.
function control(overrides = {}) {
  return {
    tag: 'BUTTON',
    role: null,
    name: null,
    testId: null,
    text: '',
    ariaLabel: null,
    title: null,
    href: null,
    className: null,
    value: null,
    shape: 'unclassified',
    ...overrides,
  };
}

test('structuralKey ignores label/class fields — same tag/role/name/testId, different text/ariaLabel/title/className => same key', () => {
  const a = control({ testId: 'save-btn', text: 'Save', ariaLabel: 'Save item', title: 'Save it', className: 'btn btn-primary' });
  const b = control({ testId: 'save-btn', text: 'Speichern', ariaLabel: 'Element speichern', title: 'Anders', className: 'btn' });
  assert.equal(structuralKey(a), structuralKey(b), 'cosmetic label/class drift must not change the structural key');
});

test('structuralKey on the common role-less path (role: null) does not throw and keys stably', () => {
  const a = control({ role: null, testId: 'delete-order' });
  assert.doesNotThrow(() => structuralKey(a));
  const b = control({ role: null, testId: 'archive-order' });
  assert.notEqual(structuralKey(a), structuralKey(b), 'same tag, different testId must produce different keys');
});

test('structuralKey stable for icon-only controls (empty text, only aria-label, no testid)', () => {
  const a = control({ ariaLabel: 'Edit' });
  const b = control({ ariaLabel: 'Edit' });
  assert.equal(structuralKey(a), structuralKey(b), 'two icon-only buttons with the same aria-label collide (documented)');
  const withTestId = control({ ariaLabel: 'Edit', testId: 'edit-row-5' });
  assert.notEqual(structuralKey(a), structuralKey(withTestId), 'adding a testId must break the collision');
});

test('diffSurfaces membership asymmetry (0-fill): admin has a testId admin has, external lacks entirely', () => {
  const perRole = [
    { role: 'admin', controls: [control({ testId: 'delete-order', ariaLabel: 'Delete', shape: 'candidate-destructive' })] },
    { role: 'external', controls: [] },
  ];
  const { diff } = diffSurfaces(perRole);
  const key = structuralKey(control({ testId: 'delete-order', ariaLabel: 'Delete', shape: 'candidate-destructive' }));
  const entry = diff.find((e) => e.key === key);
  assert.ok(entry, 'the admin-only control must appear in the diff');
  assert.deepEqual(entry.presentIn, ['admin']);
  assert.deepEqual(entry.absentIn, ['external']);
  assert.equal(entry.presence.external, 0, '0-fill: external must be an explicit 0, not simply absent from presence');
});

test('diffSurfaces count asymmetry: admin has 2 collided icon buttons, external has 1', () => {
  const perRole = [
    { role: 'admin', controls: [control({ ariaLabel: 'Edit' }), control({ ariaLabel: 'Edit' })] },
    { role: 'external', controls: [control({ ariaLabel: 'Edit' })] },
  ];
  const { diff } = diffSurfaces(perRole);
  const key = structuralKey(control({ ariaLabel: 'Edit' }));
  const entry = diff.find((e) => e.key === key);
  assert.ok(entry, 'a count-unbalanced collided key must appear in the diff');
  assert.equal(entry.presence.admin, 2);
  assert.equal(entry.presence.external, 1);
});

test('diffSurfaces no false positive: identical surfaces, and per-role-differing cosmetic fields on an otherwise-identical key', () => {
  const identical = [
    { role: 'admin', controls: [control({ testId: 'save-btn' })] },
    { role: 'external', controls: [control({ testId: 'save-btn' })] },
  ];
  assert.deepEqual(diffSurfaces(identical).diff, [], 'identical surfaces must produce an empty diff');

  const cosmeticDrift = [
    { role: 'admin', controls: [control({ testId: 'save-btn', ariaLabel: 'Save', className: 'btn-primary' })] },
    { role: 'external', controls: [control({ testId: 'save-btn', ariaLabel: 'Speichern', className: 'btn' })] },
  ];
  assert.deepEqual(diffSurfaces(cosmeticDrift).diff, [], 'per-role cosmetic drift on the same structural key must not appear as a diff');
});

test('diffSurfaces throws on duplicate role labels (fail-closed)', () => {
  const perRole = [
    { role: 'admin', controls: [] },
    { role: 'admin', controls: [] },
  ];
  assert.throws(() => diffSurfaces(perRole), /duplicate role label/);
});

test('diffSurfaces throws on a malformed entry rather than crashing mid-iteration', () => {
  assert.throws(() => diffSurfaces([{ role: 'admin' }]), TypeError, 'missing controls array');
  assert.throws(() => diffSurfaces([{ role: 'admin', controls: null }]), TypeError, 'null controls');
  assert.throws(() => diffSurfaces([{ role: 42, controls: [] }]), TypeError, 'non-string role');
  assert.throws(() => diffSurfaces(null), TypeError, 'non-array perRole');
});

test('diffSurfaces single role: diff is always empty (single-role default is harmless)', () => {
  const perRole = [
    { role: 'admin', controls: [control({ ariaLabel: 'Edit' }), control({ ariaLabel: 'Edit' }), control({ testId: 'delete-order' })] },
  ];
  const { diff } = diffSurfaces(perRole);
  assert.deepEqual(diff, []);
});

test('matrix carries 0-filled presence/presentIn/absentIn, destructive shape propagation, and the matrixLabel fallback chain', () => {
  const perRole = [
    {
      role: 'admin',
      controls: [
        control({ testId: 'delete-order', ariaLabel: 'Delete', shape: 'candidate-destructive' }),
        control({ ariaLabel: 'Filter' }), // icon-only, unclassified
        // `role` differs so this does NOT collide with the Filter control's key above (role is part
        // of the structural key but not of matrixLabel's fallback chain) — no other identity field
        // set, so matrixLabel falls back to '(unlabelled control)'.
        control({ role: 'group' }),
      ],
    },
    { role: 'external', controls: [control({ ariaLabel: 'Filter' })] },
  ];
  const { matrix } = diffSurfaces(perRole);

  const deleteEntry = matrix.find((e) => e.testId === 'delete-order');
  assert.ok(deleteEntry);
  assert.equal(deleteEntry.shape, 'candidate-destructive', 'destructive shape must propagate to the matrix entry');
  assert.deepEqual(deleteEntry.presence, { admin: 1, external: 0 }, 'presence must be 0-filled for every declared role');
  assert.deepEqual(deleteEntry.presentIn, ['admin']);
  assert.deepEqual(deleteEntry.absentIn, ['external']);

  const filterEntry = matrix.find((e) => e.label === 'Filter');
  assert.ok(filterEntry, 'icon-only control must label from ariaLabel via matrixLabel');
  assert.deepEqual(filterEntry.presence, { admin: 1, external: 1 });

  const blankEntry = matrix.find((e) => e.label === '(unlabelled control)');
  assert.ok(blankEntry, 'a fully unlabelled control must fall back to "(unlabelled control)" via matrixLabel');
});

test('documented-limitation boundary: an equal-count directional swap of icon-only controls is an intentional, locked under-report', () => {
  // admin exposes {Delete, Edit}; external exposes {Archive, Edit} — all four icon-only (no
  // role/name/testid), so Delete/Archive collide on the SAME structural key ["BUTTON", null, null,
  // null]. Both roles report count 2 for that key -> min === max -> no diff, even though external
  // lost Delete and gained Archive. This must stay true and locked — a fix that "closes" this gap by
  // widening the key with a label field would reintroduce the PII/cosmetic-drift problem test #1 and
  // #6 guard against.
  const perRole = [
    { role: 'admin', controls: [control(), control()] }, // Delete + Edit, both bare BUTTON, no identity
    { role: 'external', controls: [control(), control()] }, // Archive + Edit, same bare shape
  ];
  const { diff } = diffSurfaces(perRole);
  assert.deepEqual(diff, [], 'an equal-count directional swap of unidentifiable icon-only controls must NOT be flagged');
});
