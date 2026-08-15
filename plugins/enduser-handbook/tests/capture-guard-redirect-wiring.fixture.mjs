// Fixture for capture-guard-redirect-wiring.test.mjs — NOT a test file itself (the suite discovers
// only *.test.mjs, and this one must run under --experimental-strip-types because it imports the
// TypeScript driver).
//
// It drives the REAL installCaptureGuard against a minimal fake BrowserContext, so the seam the unit
// tests cannot reach — the context.on('request') listener wiring the audit verdict into the ledgers —
// is exercised on the shipped artifact rather than on a re-implementation of it. Each scenario prints
// one JSON line; the test file asserts on them.

import { installCaptureGuard } from '../skills/enduser-handbook/assets/capture-helpers.playwright.ts';

/** Minimal stand-in for the Playwright Request surface the guard actually calls. */
function fakeRequest({ method = 'GET', url, postData = null, resourceType = 'document', from = null }) {
  return {
    method: () => method,
    url: () => url,
    postData: () => postData,
    resourceType: () => resourceType,
    // The one call that distinguishes a hop from a browser-originated request.
    redirectedFrom: () => (from === null ? null : fakeRequest({ url: from })),
  };
}

/**
 * Minimal stand-in for BrowserContext. routeWebSocket MUST be a function or installCaptureGuard
 * throws at install time by design; `emit` replays a request to the listener the guard registered.
 */
function fakeContext() {
  const listeners = [];
  return {
    route: async () => {},
    routeWebSocket: async () => {},
    on: (event, handler) => {
      if (event === 'request') listeners.push(handler);
    },
    emit: (req) => {
      for (const handler of listeners) handler(req);
    },
    listenerCount: () => listeners.length,
  };
}

async function scenario(name, requests, guardOptions = {}) {
  const context = fakeContext();
  const guard = await installCaptureGuard(context, { denyPatterns: [], ...guardOptions });
  for (const req of requests) context.emit(fakeRequest(req));

  let threw = null;
  try {
    // Tiny drain window — nothing here is timing-dependent.
    await guard.assertNoDangerousHits(1, 20);
  } catch (err) {
    threw = err.message;
  }
  console.log(
    JSON.stringify({
      name,
      listeners: context.listenerCount(),
      redirectHops: guard.redirectHops(),
      threw,
    }),
  );
}

// 1. The #471 case: a hop into a destination the policy refuses must reach the dangerous ledger.
await scenario('dangerous-hop', [
  { method: 'GET', url: 'https://app.test/orders/42/finalize', from: 'https://app.test/reports/monthly' },
]);

// 2. A clean hop is still logged in the chain, but must NOT fail the run.
await scenario('clean-hop', [
  { method: 'GET', url: 'https://app.test/orders', from: 'https://app.test/orders/42/finalize' },
]);

// 3. A browser-originated request must be ignored by the audit channel — context.route already
//    classified it, and counting it here would double-record every blocked request.
await scenario('fresh-request-ignored', [{ method: 'POST', url: 'https://app.test/api/items', from: null }]);

// 4. A hop the project calls benign is reported in the chain but must not fail the run.
await scenario(
  'benign-hop',
  [{ method: 'POST', url: 'https://an.test/_boost/logs', from: 'https://an.test/_boost' }],
  { classifyRequest: () => 'benign' },
);

// 5. A 307-preserved POST hop keeps its own method AND body, so a body-shaped denyPattern applies.
await scenario(
  'body-shaped-deny-on-hop',
  [
    {
      method: 'POST',
      url: 'https://app.test/graphql',
      postData: '{"query":"mutation { deleteUser(id: 1) { id } }"}',
      resourceType: 'fetch',
      from: 'https://app.test/search-start',
    },
  ],
  { denyPatterns: [/\bmutation\b/] },
);
