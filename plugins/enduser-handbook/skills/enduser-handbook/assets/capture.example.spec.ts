// enduser-handbook capture asset — non-normative reference implementation for the Playwright
// reference case. The normative, engine-agnostic contract lives in
// references/capture-spec-helpers.md (and capture-safety.md / page-identity.md). Reimplement
// this driver glue for another engine; the engine-neutral lib/*.mjs helpers are reused as-is.
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
  captureRegionClipped,
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
  // Benign dev-telemetry recognizer — the AUTHOR's responsibility (there is NO built-in default).
  // Returning 'benign' BLOCKS the request (it never fires) but keeps it OUT of the dangerous ledger,
  // so assertNoDangerousHits() does not false-trip on a console-logging page (e.g. laravel-boost's
  // POST /_boost/browser-logs or a Sentry beacon). Match ONLY shapes you have verified are harmless
  // telemetry — over-broad matching would silence a real write. Tune these patterns to your stack.
  const isBenignTelemetry = (url: string): boolean => url.includes('/_boost/') || /sentry/i.test(url);

  const guard = await installCaptureGuard(context, {
    denyPatterns: ['/delete', '/send', '/approve', '/finalize'],
    // The single read/benign escape-hatch. 'benign' silences known-harmless telemetry (above);
    // otherwise defer to the SAFE GraphQL classifier — admit ONLY a single, inline, unambiguous READ
    // document ('read'), fail closed on anything else (undefined). The full ordered GraphQL rules
    // (POST /graphql inline body → no mutation/subscription → single top-level op → leading
    // `query`/`{` token) live in lib/graphql-read-classifier.mjs, unit-tested without a browser.
    // classifyRequest must be TOTAL: return undefined for anything it does not recognize, never throw
    // (it is now consulted for beacon/SSE requests too).
    classifyRequest: (req) => (isBenignTelemetry(req.url) ? 'benign' : classifyGraphqlRead(req)),
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
    // A modal can balloon to a runaway height on a layout bug; cap the shot so a stray 80,000px
    // dialog does not produce an unusable image. The cap hides content below maxHeight — paginate or
    // disclose a legitimately long region (see captureRegion's JSDoc).
    await captureRegion(dialog, `${OUTPUT_DIR}/details-dialog.png`, { maxHeight: 4000 });

    // 5d. Step: a legitimately tall modal (not a runaway-height bug) — captureRegionClipped takes a
    // single viewport-clipped shot (no scroll-stitch seam, so no shifted-offset position:fixed
    // bleed) instead of captureRegion's element-screenshot path. It scrolls the dialog to the top,
    // waits for its own open/slide transition to settle, then reports whether the full dialog fit.
    const { fitsFullHeight } = await captureRegionClipped(dialog, `${OUTPUT_DIR}/details-dialog-tall.png`);
    if (!fitsFullHeight) {
      // Disclose the truncation in the chapter prose — the shot shows the top of the dialog after
      // scroll-to-top, not the whole thing (the same discipline as captureRegion's maxHeight cap).
      // e.g.: "The screenshot shows the top of the dialog; scroll for the remaining fields."
    }
    // A dialog whose OPEN animation runs on a long ancestor transition (a slide-in panel wrapping
    // the dialog, not the dialog itself) may need a larger settle budget than the 1000ms default —
    // raise settleTimeoutMs rather than pre-emptively disabling the transition:
    // await captureRegionClipped(dialog, path, { settleTimeoutMs: 2500 });

    await dismissModal(page, { dialog, expectedText: 'Details', cancelLabel: 'Cancel' });
  } finally {
    // 6. In a finally so a delayed beacon/fetch fired during teardown is still drained and asserted
    //    before the context closes. assertNoDangerousHits is async (it awaits a quiet period).
    await guard.assertNoDangerousHits();
    await context.close();
  }
});

// State-variant capture (references/state-variants.md) — a REAL error screen the app paints itself,
// reached read-only via a bad-id route. No new guard mechanism: the guard ALLOWS the GET (it is a
// read) and the app renders its own not-found UI. No `heading` and no `apiReady`: a real 4xx/5xx
// response has no normal heading and never satisfies armApiWait's res.ok() check, so identity is
// anchored on the error-state marker instead via `state.present`.
test('capture: items chapter — error-state variant', async ({ browser }) => {
  const context = await browser.newContext({
    serviceWorkers: 'block',
    storageState: STORAGE_STATE,
  });
  const guard = await installCaptureGuard(context, {
    denyPatterns: ['/delete', '/send', '/approve', '/finalize'],
  });

  const page = await context.newPage();
  try {
    // A bad-id route the seeded role can genuinely never resolve — the app's own real not-found
    // screen, reached read-only.
    await page.goto(`${ROUTE}/does-not-exist`);
    await assertIdentity(page, { route: ROUTE, state: { present: 'Page not found' } });

    const main = page.getByRole('main');
    await main.waitFor({ state: 'visible' });
    await captureRegion(main, `${OUTPUT_DIR}/error-not-found.png`);
  } finally {
    await guard.assertNoDangerousHits();
    await context.close();
  }
});
