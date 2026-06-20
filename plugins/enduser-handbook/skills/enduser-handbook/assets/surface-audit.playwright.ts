// enduser-handbook capture asset — non-normative reference implementation for the Playwright
// reference case. The normative, engine-agnostic contract lives in
// references/capture-spec-helpers.md (and capture-safety.md / page-identity.md). Fork for other
// engines.
//
// surface-audit.playwright.ts — the mechanical surface-enumeration pass that feeds the coverage
// matrix in references/completeness-gate.md. It enumerates the live DOM (NOT the route/Blade
// source), captures every interactive trigger verbatim — including icon-only controls and status
// badges — and NEVER filters by text presence. The output is a JSON inventory plus a markdown
// coverage-matrix skeleton (one row per control) for the human classify/status pass.
//
// PII BOUNDARY (read before running): this is a mechanical, non-authoritative pass. extractRecord
// SUPPRESSES the user-data fields (a value-bearing control's textContent/value), but a control's
// IDENTITY fields — aria-label, title, name, href (e.g. a mailto address) — are logged VERBATIM
// because they ARE the label the coverage matrix needs. Those can embed PII, including names that no
// pattern mask can catch. So run this audit ONLY against seeded / non-PII data (capture-safety.md seed
// hermeticity), and the human classify pass MUST scrub any residual PII from the coverage-matrix
// labels before committing. The enumerator is a first pass, NOT a PII guarantee — see
// references/capture-safety.md and references/completeness-gate.md.
//
// CRITICAL: extraction is done with page.$$(selector) + extractRecord(handle), NOT with the
// eval-over-all variant (page.$$ + eval). That variant's callback is browser-serialized and cannot
// be shared or unit-tested; pulling extraction out into lib/control-inventory.mjs is what makes the
// "icon-only control dropped" bug catchable by tests/control-inventory.test.mjs. Do not "optimise"
// this back into the eval-over-all form.

import type { Page } from '@playwright/test';
import {
  extractRecord,
  normalizeControls,
  buildScopedSelector,
  // The broad interactive-surface selector lives in control-inventory.mjs (the audit's pure logic lib,
  // alongside buildScopedSelector) so it is unit-testable; imported here for enumeration.
  INTERACTIVE_SELECTOR,
} from './lib/control-inventory.mjs';
import { assertIdentity, armApiWait } from './capture-helpers.playwright.ts';

export interface AuditOptions {
  page: Page;
  /** Route to navigate to (signature aligned with assertIdentity). */
  route: string;
  /** Primary heading for the server-rendered identity path. */
  heading?: string;
  /** Optional XHR/response URL to await for the client-rendered identity path. */
  waitForApi?: string | RegExp;
  /** Optional CSS selector to scope enumeration within (e.g. a feature container or a row). */
  rowSelector?: string;
}

/**
 * Navigate, assert page identity, then enumerate every interactive trigger on the surface and emit
 * a JSON inventory + a coverage-matrix skeleton. Returns the normalized inventory so a caller can
 * assert against it.
 */
export async function auditSurface({ page, route, heading, waitForApi, rowSelector }: AuditOptions) {
  // Arm the API wait BEFORE navigating so a fast client-rendered response is not missed (a wait
  // registered after goto() can attach too late). Server-rendered surfaces (no waitForApi) fall back
  // to the heading identity path inside assertIdentity.
  const apiReady = waitForApi !== undefined ? armApiWait(page, waitForApi) : undefined;
  await page.goto(route);
  await assertIdentity(page, { route, heading, apiReady });

  // Scope within rowSelector if given, else the whole page. A SINGLE combined selector pass is
  // deliberate: page.$$ returns each DOM element exactly once even when it matches several parts of
  // the selector (a <button aria-label="…"> matches both `button` and `[aria-label]`), so each
  // record already corresponds to one real control. That is why normalizeControls does NO
  // content-based dedup — collapsing by extracted attributes would drop genuinely distinct controls
  // (two icon buttons 'Edit'/'Delete', repeated list rows).
  //
  // buildScopedSelector composes the scoped form (interactive descendants of the scope AND the scope
  // element itself when interactive), wrapping a possibly comma-separated rowSelector as its own
  // :is() group so it cannot leave a bare, non-interactive row container matching (whose textContent
  // would leak row PII). The pure composition is unit-tested in tests/control-inventory.test.mjs.
  const handles = await page.$$(buildScopedSelector(rowSelector, INTERACTIVE_SELECTOR));

  const raw = [];
  for (const h of handles) {
    // No text/href guard, no null returns, no .filter — extractRecord ALWAYS returns a record.
    raw.push(await extractRecord(h));
  }

  const inventory = normalizeControls(raw);

  // Machine-readable inventory.
  console.log(JSON.stringify(inventory, null, 2));

  // Human-facing coverage-matrix skeleton. One row per control; side-effect + status are TODO for
  // the human classify/status pass in completeness-gate.md.
  console.log('');
  console.log('| Trigger (verbatim UI label) | Side-effect class | Status |');
  console.log('|---|---|---|');
  for (const c of inventory) {
    const label = c.text || c.ariaLabel || c.title || c.testId || c.name || c.href || '(unlabelled control)';
    console.log(`| ${label} | TODO | TODO |`);
  }

  return inventory;
}
