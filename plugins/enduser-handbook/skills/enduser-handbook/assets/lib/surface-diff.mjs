// enduser-handbook capture asset — non-normative reference implementation for the Playwright
// reference case. The normative, engine-agnostic contract lives in
// references/surface-diff.md.
// Engine-neutral: reused as-is by any engine's driver glue.
//
// surface-diff.mjs — the pure, browser-agnostic per-role diff of the interactive surface that
// control-inventory.mjs/surface-audit.playwright.ts already enumerate. Single-role enumeration
// (capture.example.spec.ts) stays the default; this is the OPT-IN diff for when
// `capture.auth_role_enum` lists more than one role and the reader wants to see how the interactive
// surface differs between roles. Plain ESM, no browser dependency, unit-testable under
// `node --test` (tests/surface-diff.test.mjs) — the Playwright driver that runs the single-role
// enumeration once per role and feeds diffSurfaces lives separately, in
// assets/reaudit.example.spec.ts.
//
// A plain runtime `import { matrixLabel } from './control-inventory.mjs'` is ORDINARY, LEGAL ESM in
// a .mjs file — it is REQUIRED here for the display-only `label` field below, so this diff can never
// drift from the audit's own label fallback chain (a local copy would risk silently reintroducing the
// pre-#52 `(unlabelled control)` bug in the per-role diff — single source of truth). What is
// FORBIDDEN is a TypeScript-only type-only ES import — a genuine SyntaxError under `node --test`.
// The `NormalizedControl` subset this file reads is therefore documented ONLY via a local JSDoc
// @typedef below, never an ES type-import.
import { matrixLabel } from './control-inventory.mjs';

/**
 * The subset of control-inventory.mjs's NormalizedControl this module reads. A LOCAL JSDoc typedef
 * (never an ES type-only import) — see the banner above.
 *
 * @typedef {{ tag: string|null, role: string|null, name: string|null, testId: string|null,
 *             text?: string|null, ariaLabel?: string|null, title?: string|null, href?: string|null,
 *             className?: string|null, value?: string|null,
 *             shape?: 'candidate-destructive'|'unclassified' }} NormalizedControlSubset
 */

/**
 * @typedef {{ role: string, controls: NormalizedControlSubset[] }} RoleInventory
 */

/**
 * The structural, PII-free identity of a control — the tuple `[tag, role, name, testId]`. NEVER the
 * v1.0.6-broadened label/class fields (text/ariaLabel/title/className), nor href, so a cosmetic or
 * per-seed labelling difference between roles cannot masquerade as a surface difference. This
 * exclusion is the real contract, gated behaviorally by tests/surface-diff.test.mjs #1 — this JSDoc
 * line documents the key ORDER for the (cosmetic) sentinel in tests/reference-assets.test.sh.
 *
 * `role` is null-guarded: `role` is null on the majority of controls (control-inventory.d.mts), and a
 * naive `.toLowerCase()` would throw on that common path. `JSON.stringify` coerces `undefined`/absent
 * fields to `null` inside an array, so a partial record degrades to an all-null key rather than
 * crashing.
 *
 * href, text, ariaLabel, title, and className are deliberately excluded — see
 * references/surface-diff.md for why (href in particular embeds per-seed record ids that would
 * otherwise fabricate a diff between two identical roles).
 *
 * @param {NormalizedControlSubset} record
 * @returns {string}
 */
export function structuralKey(record) {
  const roleKey = record.role == null ? null : record.role.toLowerCase();
  return JSON.stringify([record.tag, roleKey, record.name, record.testId]);
}

/**
 * Diff the interactive surface across roles, keyed on `structuralKey`. Counts are 0-filled across the
 * FULL declared role set (every role in `perRole`) BEFORE any control is counted — load-bearing: a
 * naive "only record a count where a key was actually seen" implementation would let a
 * membership-asymmetric key (present for one role, entirely absent for another) escape the diff,
 * because the missing role's count would simply be absent from the presence map rather than 0. See
 * tests/surface-diff.test.mjs #4.
 *
 * Fails CLOSED (throws) on a malformed entry or a duplicate role label — this is a diagnostic tool fed
 * by the driver's own enumeration, so a shape defect should surface immediately rather than silently
 * producing a wrong diff from suspect data. Validation runs to completion BEFORE any control is
 * touched, so a throw never leaves a partial result behind.
 *
 * @typedef {{ key: string, tag: string|null, role: string|null, name: string|null,
 *             testId: string|null, label: string, shape: 'candidate-destructive'|'unclassified',
 *             presence: Record<string, number>, presentIn: string[], absentIn: string[] }} SurfaceDiffEntry
 *
 * @param {RoleInventory[]} perRole
 * @returns {{ roles: string[], matrix: SurfaceDiffEntry[], diff: SurfaceDiffEntry[] }}
 */
export function diffSurfaces(perRole) {
  if (!Array.isArray(perRole)) {
    throw new TypeError('diffSurfaces: perRole must be an array of { role, controls } entries');
  }

  // Validate every entry AND collect the declared role set in one pass, fully, before any control is
  // read — a throw here must never leave a partial matrix/diff behind.
  const roles = [];
  for (const entry of perRole) {
    if (entry == null || typeof entry.role !== 'string' || !Array.isArray(entry.controls)) {
      throw new TypeError(
        'diffSurfaces: each entry must be { role: string, controls: NormalizedControl[] }',
      );
    }
    if (roles.includes(entry.role)) {
      throw new TypeError(`diffSurfaces: duplicate role label "${entry.role}"`);
    }
    roles.push(entry.role);
  }

  // key -> { record (first contributing control, for the display fields), presence, shape }
  const byKey = new Map();

  for (const entry of perRole) {
    for (const control of entry.controls) {
      const key = structuralKey(control);
      let bucket = byKey.get(key);
      if (!bucket) {
        // 0-fill across the FULL declared role set up front — see the JSDoc above.
        const presence = {};
        for (const r of roles) presence[r] = 0;
        bucket = { record: control, presence, shape: 'unclassified' };
        byKey.set(key, bucket);
      }
      bucket.presence[entry.role] += 1;
      if (control.shape === 'candidate-destructive') bucket.shape = 'candidate-destructive';
    }
  }

  const matrix = [];
  for (const [key, bucket] of byKey) {
    const presentIn = roles.filter((r) => bucket.presence[r] > 0);
    const absentIn = roles.filter((r) => bucket.presence[r] === 0);
    matrix.push({
      key,
      tag: bucket.record.tag ?? null,
      role: bucket.record.role ?? null,
      name: bucket.record.name ?? null,
      testId: bucket.record.testId ?? null,
      // Display-only label — the SAME fallback chain the surface audit itself uses, imported (not
      // inlined) so this diff can never drift from it. Carries the audit's own PII boundary.
      label: matrixLabel(bucket.record),
      shape: bucket.shape,
      presence: bucket.presence,
      presentIn,
      absentIn,
    });
  }
  // Deterministic output regardless of which role happened to be enumerated first (Map iteration
  // order is insertion order, which tracks perRole order, not a stable content order).
  matrix.sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));

  // The diff: every key whose per-role counts are not all equal, over the FULL declared role set
  // (roles.length === 1 → min === max always → diff is trivially empty; the single-role default is
  // harmless to run through this function).
  const diff = matrix.filter((entry) => {
    const counts = roles.map((r) => entry.presence[r]);
    return Math.min(...counts) !== Math.max(...counts);
  });

  return { roles, matrix, diff };
}
