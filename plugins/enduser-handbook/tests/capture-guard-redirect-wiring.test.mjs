// Integration test for the redirect-hop SEAM (#471): the context.on('request') listener inside
// installCaptureGuard that turns an audit verdict into ledger entries.
//
// Why this exists as its own file. capture-guard-policy.test.mjs proves the pure CLASSIFICATION is
// right, and reference-assets.test.sh proves the wiring is PRESENT as text — neither proves the
// wiring WORKS. Deleting the recorded push would leave a correct verdict computed and never recorded,
// which is #471's original failure exactly: a dangerous request fires and the run reports clean.
//
// The driver is TypeScript, so the actual driving happens in a sibling fixture run under
// --experimental-strip-types (Node >= 22.6); this file spawns it and asserts on its output. The
// fixture drives the REAL installCaptureGuard against a fake BrowserContext — a stub of the ENGINE,
// not a re-implementation of the guard. It is corroborated by a real-Chromium measurement recorded in
// the commit history; two methods, one shipped artifact.

import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURE = join(HERE, 'capture-guard-redirect-wiring.fixture.mjs');

// Node strips TypeScript only from 22.6 onward. Probe rather than assume, so an older runtime reports
// a visible SKIP instead of a confusing failure — and so the skip can never be mistaken for a pass.
const probe = spawnSync(process.execPath, ['--experimental-strip-types', '-e', ''], { encoding: 'utf8' });
const STRIP_TYPES = probe.status === 0;

const run = () => {
  const r = spawnSync(process.execPath, ['--experimental-strip-types', FIXTURE], { encoding: 'utf8' });
  assert.equal(r.status, 0, `fixture exited ${r.status}\nstdout:\n${r.stdout}\nstderr:\n${r.stderr}`);
  const byName = new Map();
  for (const line of r.stdout.split('\n').filter((l) => l.trim().length > 0)) {
    const row = JSON.parse(line);
    byName.set(row.name, row);
  }
  return byName;
};

test('the guard registers exactly one context-level request listener', { skip: !STRIP_TYPES && 'needs Node >= 22.6 for --experimental-strip-types' }, () => {
  // A page-level or missing registration is the silent-failure shape: the guard installs, the run is
  // green, and no hop is ever seen.
  assert.equal(run().get('dangerous-hop').listeners, 1);
});

test('a dangerous redirect hop reaches the dangerous ledger and fails the assertion', { skip: !STRIP_TYPES && 'needs Node >= 22.6 for --experimental-strip-types' }, () => {
  const row = run().get('dangerous-hop');
  assert.deepEqual(row.redirectHops, [
    'redirect-hop:deny-dangerous-verb [dangerous]: GET https://app.test/orders/42/finalize <- https://app.test/reports/monthly',
  ]);
  assert.notEqual(row.threw, null, 'assertNoDangerousHits must throw on a dangerous hop');
  assert.match(row.threw, /redirect-hop:deny-dangerous-verb: GET https:\/\/app\.test\/orders\/42\/finalize/);
  assert.match(row.threw, /redirected from https:\/\/app\.test\/reports\/monthly/);
  // The message must say the hop FIRED rather than implying it was stopped.
  assert.match(row.threw, /DETECTED, not blocked/);
});

test('a clean redirect hop is logged in the chain but does not fail the run', { skip: !STRIP_TYPES && 'needs Node >= 22.6 for --experimental-strip-types' }, () => {
  const row = run().get('clean-hop');
  assert.deepEqual(row.redirectHops, [
    'redirect-hop:get-head [clean]: GET https://app.test/orders <- https://app.test/orders/42/finalize',
  ]);
  assert.equal(row.threw, null, 'a plain read hop must not fail the run');
});

test('a browser-originated request is ignored by the audit channel', { skip: !STRIP_TYPES && 'needs Node >= 22.6 for --experimental-strip-types' }, () => {
  // context.route already classified it; auditing it here would double-record every blocked request.
  const row = run().get('fresh-request-ignored');
  assert.deepEqual(row.redirectHops, []);
  assert.equal(row.threw, null);
});

test('a benign hop is reported in the chain but never counted dangerous', { skip: !STRIP_TYPES && 'needs Node >= 22.6 for --experimental-strip-types' }, () => {
  const row = run().get('benign-hop');
  assert.deepEqual(row.redirectHops, [
    'redirect-hop:classify-benign [benign]: POST https://an.test/_boost/logs <- https://an.test/_boost',
  ]);
  assert.equal(row.threw, null, 'a benign hop must not fail the run');
});

test('a body-shaped denyPattern reaches a hop through the listener, not just the pure policy', { skip: !STRIP_TYPES && 'needs Node >= 22.6 for --experimental-strip-types' }, () => {
  // Pins that the listener passes postData into the audit: dropping it downgrades this to a generic
  // fail-closed, which is still dangerous, so only the REASON reveals the regression.
  const row = run().get('body-shaped-deny-on-hop');
  assert.deepEqual(row.redirectHops, [
    'redirect-hop:deny-pattern [dangerous]: POST https://app.test/graphql <- https://app.test/search-start',
  ]);
  assert.match(row.threw, /redirect-hop:deny-pattern/);
});
