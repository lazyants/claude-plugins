// enduser-handbook capture asset — non-normative reference implementation for the Playwright
// reference case. The normative, engine-agnostic contract lives in
// references/capture-spec-helpers.md (and capture-safety.md / page-identity.md). Fork for other
// engines.
//
// capture.example.spec.ts — a skeleton chapter capture spec wiring the canonical, safe flow. Copy
// it into the project's capture.capture_specs_dir and adapt the route/heading/selectors/labels to
// the accepted manifest entry. It is intentionally minimal and copyable, not a runnable test of
// any real app.
//
// Canonical order (load-bearing):
//   1. create the context with serviceWorkers: 'block' + a pre-seeded storageState (no live login);
//   2. installCaptureGuard(context, …) BEFORE the first page is created;
//   3. context.newPage();
//   4. assertIdentity before every shot;
//   5. per step: assert the element → captureRegion / openModalDialog+dismissModal → maskAndAssert
//      where PII would appear;
//   6. assertNoDangerousHits() at the end.

import { test } from '@playwright/test';
import {
  installCaptureGuard,
  assertIdentity,
  captureRegion,
  openModalDialog,
  dismissModal,
  maskAndAssert,
} from './capture-helpers.playwright.ts';
import { auditSurface } from './surface-audit.playwright.ts';
import { classifyGraphqlRead } from './lib/graphql-read-classifier.mjs';

// Project-specific values — replace with the accepted manifest entry's data.
const ROUTE = '/items';
const HEADING = 'Items'; // verbatim primary heading for the server-rendered identity path
const STORAGE_STATE = 'storage/seeded-user.json'; // pre-seeded auth; NEVER a live login
const OUTPUT_DIR = 'handbook/assets/items';

test('capture: items chapter', async ({ browser }) => {
  // 1. Context with service workers blocked (so context.route cannot be bypassed) + seeded auth.
  const context = await browser.newContext({
    serviceWorkers: 'block',
    storageState: STORAGE_STATE,
  });

  // 2. Install the guard at context level, BEFORE any page exists. denyPatterns are seeded from the
  //    project's capture.live_action_examples; classifyRequest admits read-only POSTs (GraphQL
  //    queries) for POST-read apps. This is defense-in-depth — the human classify pass still
  //    governs every click below.
  const guard = await installCaptureGuard(context, {
    denyPatterns: ['/delete', '/send', '/approve', '/finalize'],
    // The SAFE GraphQL pattern — the guard's single allow escape-hatch. Admit ONLY a single, inline,
    // unambiguous READ document; fail closed on anything else. The full ordered rules (POST /graphql
    // inline body → no mutation/subscription → single top-level op → leading `query`/`{` token) live
    // in lib/graphql-read-classifier.mjs, where they are unit-tested without a browser.
    classifyRequest: (req) => classifyGraphqlRead(req),
  });

  // 3. Only now create the page.
  const page = await context.newPage();
  try {
    await page.goto(ROUTE);

    // 4. Assert identity before the first shot (server-rendered path — assert the heading/DOM).
    await assertIdentity(page, { route: ROUTE, heading: HEADING });

    // 5a. Optional: run the mechanical surface-enumeration pass to seed the coverage matrix.
    await auditSurface({ page, route: ROUTE, heading: HEADING });

    // 5b. Step: capture the overview region.
    const list = page.getByRole('main');
    await list.waitFor({ state: 'visible' });
    await captureRegion(list, `${OUTPUT_DIR}/overview.png`);

    // 5c. Step: open a dialog, mask the PII it shows, capture, then dismiss SAFELY (Escape first).
    const dialog = await openModalDialog(page, page.getByRole('button', { name: 'Details' }));
    await maskAndAssert(page, {
      dialog,
      selectors: ['.customer-name', '.customer-email'],
      placeholder: '••••••',
      patterns: [/[\w.+-]+@[\w-]+\.[\w.-]+/], // e-mail leak pattern; add project-specific patterns
      expectedCount: 2,
    });
    await captureRegion(dialog, `${OUTPUT_DIR}/details-dialog.png`);
    await dismissModal(page, { dialog, expectedText: 'Details', cancelLabel: 'Cancel' });
  } finally {
    // 6. In a finally so a delayed beacon/fetch fired during teardown is still drained and asserted
    //    before the context closes. assertNoDangerousHits is async (it awaits a quiet period).
    await guard.assertNoDangerousHits();
    await context.close();
  }
});
