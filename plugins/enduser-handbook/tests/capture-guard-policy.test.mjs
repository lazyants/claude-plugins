// Unit tests for the pure capture-guard classifier. Zero deps — runs under Node's built-in test
// runner: `node --test capture-guard-policy.test.mjs`.
//
// decideRoute is the real contract for the guard's branch order and fail-closed behavior. Asserting
// it directly (rather than only grepping sentinel comment positions) is what catches a reordering of
// the actual decisions — e.g. a dangerous GET reaching the get-head allow because the deny step ran
// after it.

import test from 'node:test';
import assert from 'node:assert/strict';

import {
  decideRoute,
  tokenize,
  hasDangerousVerb,
  matchesDeny,
} from '../skills/enduser-handbook/assets/lib/capture-guard-policy.mjs';

// Build a GuardRequest with sensible defaults.
function req(over = {}) {
  return {
    method: 'GET',
    url: 'https://app.test/',
    postData: null,
    resourceType: 'fetch',
    ...over,
  };
}

test('deny patterns block regardless of method (string + RegExp)', () => {
  assert.equal(decideRoute(req({ method: 'GET', url: 'https://app.test/x/send' }), { denyPatterns: ['/send'] }).action, 'block');
  assert.equal(decideRoute(req({ method: 'POST', url: 'https://app.test/x/y' }), { denyPatterns: [/\/x\//] }).action, 'block');
});

test('deny RegExp matching is stateless even with a /g flag', () => {
  const g = /\/danger\//g;
  // A stateful .test would alternate true/false on repeated calls; matchesDeny must be deterministic.
  const r = req({ url: 'https://app.test/danger/now' });
  assert.equal(matchesDeny(r, [g]), true);
  assert.equal(matchesDeny(r, [g]), true);
  assert.equal(decideRoute(r, { denyPatterns: [g] }).action, 'block');
});

test('deny RegExp matching is stateless even with a /y (sticky) flag', () => {
  // /y advances lastIndex just like /g, so a sticky deny pattern would otherwise return false on the
  // SECOND call and silently stop blocking. matchesDeny rebuilds flagless ([gy] stripped) — assert it.
  const y = /\/danger\//y;
  const r = req({ url: 'https://app.test/danger/now' });
  assert.equal(matchesDeny(r, [y]), true);
  assert.equal(matchesDeny(r, [y]), true);
  assert.equal(decideRoute(r, { denyPatterns: [y] }).action, 'block');
});

test('deny patterns also match the request BODY (postData), not just the URL', () => {
  // A GraphQL mutation POSTed to a generic /graphql endpoint: method+url alone do not match, but a
  // body-targeting denyPattern must still block it (backstop for a classifyRequest that wrongly
  // returns 'read'). This is the prior-fix-5 gap.
  const mutation = req({
    method: 'POST',
    url: 'https://app.test/graphql',
    postData: '{"query":"mutation { deleteUser(id: 1) { id } }"}',
  });
  // matchesDeny sees the body.
  assert.equal(matchesDeny(mutation, [/\bmutation\b/]), true);
  // Even if classifyRequest wrongly admits it, deny runs first and blocks.
  assert.equal(
    decideRoute(mutation, { denyPatterns: [/\bmutation\b/], classifyRequest: () => 'read' }).action,
    'block',
  );
  // A read query body with the same denyPattern is NOT blocked by it (no 'mutation' token in body).
  const query = req({ method: 'POST', url: 'https://app.test/graphql', postData: '{"query":"query { me { id } }"}' });
  assert.equal(matchesDeny(query, [/\bmutation\b/]), false);
  // A string denyPattern works against the body too.
  assert.equal(matchesDeny(mutation, ['deleteUser']), true);
});

test('built-in dangerous-verb GET is blocked even when not in denyPatterns', () => {
  // The exact finding-4 case: a destructive GET the author forgot to list.
  assert.equal(decideRoute(req({ method: 'GET', url: 'https://app.test/items/5/disable' })).reason, 'deny-dangerous-verb');
  assert.equal(decideRoute(req({ method: 'GET', url: 'https://app.test/users/7/delete-now' })).reason, 'deny-dangerous-verb');
  // dangerous-verb runs BEFORE get-head, so a plain GET to such a URL never reaches the allow.
  assert.equal(decideRoute(req({ method: 'GET', url: 'https://app.test/users/7/delete-now' })).action, 'block');
});

test('eventsource (SSE) blocks by default, allowed via classifyRequest read', () => {
  assert.equal(decideRoute(req({ resourceType: 'eventsource', url: 'https://ext.test/stream' })).reason, 'eventsource');
  const allow = decideRoute(req({ resourceType: 'eventsource', url: 'https://app.test/stream' }), {
    classifyRequest: () => 'read',
  });
  assert.equal(allow.action, 'allow');
  assert.equal(allow.reason, 'eventsource-read');
});

test('beacon (ping) blocks by default, allowed via allowBeacons; denyPatterns still win', () => {
  assert.equal(decideRoute(req({ resourceType: 'ping', url: 'https://an.test/collect' })).reason, 'beacon');
  assert.equal(decideRoute(req({ resourceType: 'ping', url: 'https://an.test/collect' }), { allowBeacons: true }).action, 'allow');
  // deny wins over allowBeacons.
  assert.equal(
    decideRoute(req({ resourceType: 'ping', url: 'https://an.test/collect' }), { allowBeacons: true, denyPatterns: ['/collect'] }).action,
    'block',
  );
});

test('classify-read admits a GraphQL query; only exact "read" admits', () => {
  const classifyRequest = (r) => (r.postData?.includes('query') && !r.postData?.includes('mutation') ? 'read' : undefined);
  assert.equal(decideRoute(req({ method: 'POST', url: 'https://app.test/graphql', postData: '{"query":"query{me}"}' }), { classifyRequest }).action, 'allow');
  // A mutation is not admitted → fail-closed POST.
  assert.equal(decideRoute(req({ method: 'POST', url: 'https://app.test/graphql', postData: '{"query":"mutation{deleteUser}"}' }), { classifyRequest }).action, 'block');
  // Any non-'read' return (e.g. true) does NOT admit.
  assert.equal(decideRoute(req({ method: 'POST', url: 'https://app.test/x', postData: 'x' }), { classifyRequest: () => true }).action, 'block');
});

test('classify-benign blocks (never allows) and wins over eventsource/beacon/fail-closed', () => {
  // 'benign' means "known-harmless telemetry": block it (it never fires) but it is NOT counted
  // dangerous. It must win over eventsource, beacon, and the fail-closed default — but never allow.
  const benign = { classifyRequest: () => 'benign' };
  // A fail-closed POST is now silenced as benign instead.
  const post = decideRoute(req({ method: 'POST', url: 'https://an.test/_boost/logs' }), benign);
  assert.equal(post.action, 'block');
  assert.equal(post.reason, 'classify-benign');
  // A ping/beacon: benign wins over the beacon branch (the predicate is now reached for beacons).
  const beacon = decideRoute(req({ resourceType: 'ping', url: 'https://an.test/collect' }), benign);
  assert.equal(beacon.action, 'block');
  assert.equal(beacon.reason, 'classify-benign');
  // An eventsource/SSE: benign wins over the eventsource branch.
  const sse = decideRoute(req({ resourceType: 'eventsource', url: 'https://an.test/stream' }), benign);
  assert.equal(sse.action, 'block');
  assert.equal(sse.reason, 'classify-benign');
});

test('deny still wins over a benign verdict', () => {
  // deny patterns + the built-in dangerous-verb block run BEFORE the hoisted classifier, so a
  // 'benign' verdict cannot reopen a denied/destructive request.
  assert.equal(
    decideRoute(req({ method: 'POST', url: 'https://an.test/collect' }), {
      denyPatterns: ['/collect'],
      classifyRequest: () => 'benign',
    }).reason,
    'deny-pattern',
  );
  assert.equal(
    decideRoute(req({ method: 'GET', url: 'https://app.test/items/5/delete' }), {
      classifyRequest: () => 'benign',
    }).reason,
    'deny-dangerous-verb',
  );
});

test('a stray-truthy classifyRequest still fails closed (only exact "read"/"benign" act)', () => {
  // Neither 'read' nor 'benign' — a stray truthy must not admit nor silence; the POST fails closed.
  assert.equal(decideRoute(req({ method: 'POST', url: 'https://app.test/x', postData: 'x' }), { classifyRequest: () => true }).reason, 'fail-closed');
});

test('plain GET/HEAD are allowed (after the deny step has cleared dangerous-verb GETs)', () => {
  assert.equal(decideRoute(req({ method: 'GET', url: 'https://cdn.test/font.woff2' })).action, 'allow');
  assert.equal(decideRoute(req({ method: 'HEAD', url: 'https://app.test/health' })).action, 'allow');
});

test('unclassified non-GET fails closed', () => {
  assert.equal(decideRoute(req({ method: 'POST', url: 'https://app.test/api/x' })).reason, 'fail-closed');
  assert.equal(decideRoute(req({ method: 'PUT', url: 'https://app.test/api/x' })).action, 'block');
  assert.equal(decideRoute(req({ method: 'DELETE', url: 'https://app.test/api/x' })).action, 'block');
});

test('tokenize splits camelCase / snake / kebab / URL boundaries', () => {
  assert.deepEqual(tokenize('deleteUser'), ['delete', 'user']);
  assert.deepEqual(tokenize('/users/7/delete-now'), ['users', '7', 'delete', 'now']);
  assert.deepEqual(tokenize('remove_item'), ['remove', 'item']);
});

test('hasDangerousVerb is token-exact (deletedAt does not match)', () => {
  assert.equal(hasDangerousVerb('https://app.test/items/5/delete'), true);
  assert.equal(hasDangerousVerb('https://app.test/records?deletedAt=1'), false);
  assert.equal(hasDangerousVerb('/relative/destroy/path'), true);
});

test('hasDangerousVerb decodes percent-encoded paths and scans the query string', () => {
  // Percent-encoded "delete" → /items/%64elete must not slip through.
  assert.equal(hasDangerousVerb('https://app.test/items/%64elete'), true);
  // Percent-encoded German "löschen".
  assert.equal(hasDangerousVerb('https://app.test/settings/l%C3%B6schen'), true);
  // A dangerous verb in the query string (?action=delete) is caught.
  assert.equal(hasDangerousVerb('https://app.test/items?action=delete'), true);
  // A malformed percent sequence must not throw — falls back to scanning the raw value.
  assert.equal(hasDangerousVerb('https://app.test/items/%E0%A4%A/delete'), true);
  // A malformed escape co-located with a VALIDLY-encoded dangerous verb in the SAME segment must
  // still catch the verb (per-run decode, not all-or-nothing): "%64elete" decodes to "delete".
  assert.equal(hasDangerousVerb('https://app.test/items/%64elete/%zz'), true);
  // A run that is ALL valid hex yet invalid UTF-8 ("%E0%A4%64": "%64"='d' wedged inside a broken
  // 3-byte lead) must still catch the verb — decodeURIComponent throws on the whole run, and the
  // byte-level fallback recovers the ASCII bytes so "delete" survives as a token.
  assert.equal(hasDangerousVerb('https://app.test/items/%E0%A4%64elete'), true);
  // A safe multibyte segment alone must not false-positive.
  assert.equal(hasDangerousVerb('https://app.test/caf%C3%A9/list'), false);
});

test('decideRoute blocks a percent-encoded dangerous GET via the deny step', () => {
  assert.equal(decideRoute(req({ method: 'GET', url: 'https://app.test/items/%64elete' })).reason, 'deny-dangerous-verb');
});
