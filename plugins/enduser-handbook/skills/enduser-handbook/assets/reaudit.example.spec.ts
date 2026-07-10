// enduser-handbook capture asset — non-normative reference implementation for the Playwright
// reference case. The normative, engine-agnostic contract lives in
// references/surface-diff.md.
// Engine-neutral: reused as-is by any engine's driver glue.
//
// reaudit.example.spec.ts — the OPT-IN per-role driver: runs the mechanical surface-enumeration pass
// (surface-audit.playwright.ts's auditSurface) ONCE PER ROLE, each against its own seeded, hermetic
// browser.newContext({ storageState }) — the exact in-repo pattern at capture.example.spec.ts:40-43 —
// then diffs the collected inventories with diffSurfaces (lib/surface-diff.mjs). Single-role
// enumeration stays the default (capture.example.spec.ts is untouched by this file); this driver is
// opt-in, for when `capture.auth_role_enum` lists more than one role and you want to see how the
// interactive surface differs between roles.
//
// The roles list (label + storageState path) lives HERE, in the spec — not the profile.
// references/manifest-discipline.md is explicit that storage-state paths are a spec-level artifact
// ("Not a place to encode engine APIs... storage state paths... live in the capture spec"), and the
// existing single STORAGE_STATE const already lives in capture.example.spec.ts:35. The profile keeps
// only `capture.auth_role_enum` as the role vocabulary — each AUDIT_ROLES `label` below must be a
// member of it.
//
// PARALLEL HERMETIC SEEDING (load-bearing): each role's storageState must point at its OWN seeded
// fixture carrying the SAME underlying records, differing only by role/permission — never a fixture
// that also varies the underlying data. Otherwise a count difference in the diff below could be a
// data artifact, not a permission one, and the diff would be misleading rather than a genuine
// role-axis comparison. See references/surface-diff.md and references/state-variants.md (the sibling
// DATA-STATE axis — populated/empty/error/denied — which this file does not vary).
//
// PII BOUNDARY: identical to surface-audit.playwright.ts — the enumerator is a mechanical,
// non-authoritative first pass. Run it ONLY against seeded / non-PII data, and scrub the human
// classify pass before committing. See references/surface-diff.md for the full methodology,
// including the documented weak-key / equal-count-swap detection limits (an icon-only, testid-less
// directional swap between roles is an undetectable under-report — never a fabrication).

import { test } from '@playwright/test';
import { installCaptureGuard } from './capture-helpers.playwright.ts';
import { auditSurface } from './surface-audit.playwright.ts';
import { diffSurfaces } from './lib/surface-diff.mjs';
import type { NormalizedControl } from './lib/control-inventory.mjs';

// Project-specific values — replace with the accepted manifest entry's data.
const ROUTE = '/items';
const HEADING = 'Items'; // verbatim primary heading for the server-rendered identity path

// Each `label` MUST be a member of the profile's `capture.auth_role_enum`. Each `storageState` is its
// own hermetic, pre-seeded fixture — see the PARALLEL HERMETIC SEEDING note above.
const AUDIT_ROLES: { label: string; storageState: string }[] = [
  { label: 'admin', storageState: 'storage/seeded-admin.json' },
  { label: 'external', storageState: 'storage/seeded-external.json' },
];

test('re-audit: items chapter, per role', async ({ browser }) => {
  const perRole: { role: string; controls: NormalizedControl[] }[] = [];

  for (const { label, storageState } of AUDIT_ROLES) {
    // Canonical order per role, mirroring capture.example.spec.ts: context with service workers
    // blocked + a pre-seeded storageState (no live login), THEN installCaptureGuard BEFORE any page
    // exists, THEN newPage.
    const context = await browser.newContext({ serviceWorkers: 'block', storageState });
    const guard = await installCaptureGuard(context, {
      // Tune to your stack, exactly like capture.example.spec.ts's denyPatterns. A read-admitting
      // classifyRequest (e.g. classifyGraphqlRead for a POST-read GraphQL app) can be added the same
      // way as in capture.example.spec.ts; omitted here for brevity.
      denyPatterns: ['/delete', '/send', '/approve', '/finalize'],
    });
    const page = await context.newPage();
    try {
      // auditSurface itself navigates and asserts identity before enumerating — no separate goto/
      // assertIdentity call is needed here.
      const controls = await auditSurface({ page, route: ROUTE, heading: HEADING });
      perRole.push({ role: label, controls });
    } finally {
      // In a finally so a delayed beacon/fetch fired during this role's pass is still drained and
      // asserted before its context closes — mirrors capture.example.spec.ts.
      await guard.assertNoDangerousHits();
      await context.close();
    }
  }

  const { roles, matrix, diff } = diffSurfaces(perRole);

  // Human-facing per-role matrix: one row per distinct control, one count column per role.
  console.log('');
  console.log(`Roles audited: ${roles.join(', ')}`);
  console.log('');
  console.log(`| Control (verbatim label) | ${roles.join(' | ')} | Side-effect class |`);
  console.log(`|---|${roles.map(() => '---').join('|')}|---|`);
  for (const entry of matrix) {
    const counts = roles.map((r) => entry.presence[r]).join(' | ');
    console.log(`| ${entry.label} | ${counts} | ${entry.shape} |`);
  }

  // The diff — the subset a reader should actually look at: membership or count asymmetry between
  // roles, single-role remains harmless (diff is always empty for one role).
  console.log('');
  console.log('== Per-role diff (membership or count asymmetry) ==');
  console.log('');
  console.log('| Control (verbatim label) | Present in | Absent in | Side-effect class |');
  console.log('|---|---|---|---|');
  for (const entry of diff) {
    console.log(
      `| ${entry.label} | ${entry.presentIn.join(', ')} | ${entry.absentIn.join(', ')} | ${entry.shape} |`,
    );
  }
});
