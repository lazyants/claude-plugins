// Unit tests for the pure GraphQL read-classifier. Zero deps — runs under Node's built-in test
// runner: `node --test graphql-read-classifier.test.mjs`.
//
// classifyGraphqlRead is the capture guard's SINGLE allow escape-hatch — the one place that can turn
// an otherwise fail-closed POST into a 'read'. These tests pin its exact behavior so the extraction
// from capture.example.spec.ts stays a pure refactor and a later edit cannot silently widen what it
// admits.

import test from 'node:test';
import assert from 'node:assert/strict';

import { classifyGraphqlRead } from '../skills/enduser-handbook/assets/lib/graphql-read-classifier.mjs';

// Build a POST /graphql request whose JSON body carries `query`.
function gql(query) {
  return { method: 'POST', url: 'https://app.test/graphql', postData: JSON.stringify({ query }) };
}

test('admits a plain inline read — named query and anonymous shorthand', () => {
  assert.equal(classifyGraphqlRead(gql('query { me }')), 'read');
  assert.equal(classifyGraphqlRead(gql('{ me }')), 'read');
});

test('a field merely NAMED mutationLog is still a read (no camelCase word boundary)', () => {
  // \b(mutation|subscription)\b does not match inside "mutationLog" — there is no word boundary
  // between "n" and "L". Preserve the inline code's true behavior: this is admitted, NOT rejected.
  assert.equal(classifyGraphqlRead(gql('query { mutationLog }')), 'read');
  assert.equal(classifyGraphqlRead(gql('{ mutationLog }')), 'read');
  assert.equal(classifyGraphqlRead(gql('query { subscriptionFeed }')), 'read');
});

test('fails closed on a mixed query+mutation document', () => {
  assert.equal(classifyGraphqlRead(gql('query Q { a } mutation M { b }')), undefined);
});

test('fails closed on a comment-prefixed mutation', () => {
  assert.equal(classifyGraphqlRead(gql('# c\nmutation { x }')), undefined);
});

test('fails closed on a subscription', () => {
  assert.equal(classifyGraphqlRead(gql('subscription { x }')), undefined);
});

test('fails closed on a multi-op document (two query keywords)', () => {
  assert.equal(classifyGraphqlRead(gql('query A { a } query B { b }')), undefined);
});

test('fails closed on a persisted op (query not an inline string)', () => {
  // No inline `query` string — e.g. a persisted op carrying only a queryId.
  assert.equal(
    classifyGraphqlRead({ method: 'POST', url: 'https://app.test/graphql', postData: JSON.stringify({ queryId: 'abc123' }) }),
    undefined,
  );
  assert.equal(classifyGraphqlRead(gql(42)), undefined); // non-string query value
});

test('fails closed on an unparseable body', () => {
  assert.equal(classifyGraphqlRead({ method: 'POST', url: 'https://app.test/graphql', postData: '{not json' }), undefined);
});

test('fails closed on a fragment-only document', () => {
  assert.equal(classifyGraphqlRead(gql('fragment F on T { id }')), undefined);
});

test('fails closed on non-POST methods', () => {
  assert.equal(classifyGraphqlRead({ method: 'GET', url: 'https://app.test/graphql', postData: JSON.stringify({ query: '{ me }' }) }), undefined);
});

test('returns undefined (does not throw) on beacon/SSE-shaped requests — predicate totality', () => {
  // The capture guard now consults classifyRequest for ping/beacon and eventsource requests too
  // (decideRoute hoists it above those branches), so it MUST be total: return undefined for any
  // request it does not recognize and never throw. These shapes are not POST /graphql with an inline
  // read body, so they must fall through cleanly to undefined.
  const beacon = { method: 'POST', url: 'https://an.test/collect', postData: 'metric=1', resourceType: 'ping' };
  const beaconGet = { method: 'GET', url: 'https://an.test/collect', postData: null, resourceType: 'ping' };
  const sse = { method: 'GET', url: 'https://app.test/stream', postData: null, resourceType: 'eventsource' };
  assert.doesNotThrow(() => classifyGraphqlRead(beacon));
  assert.doesNotThrow(() => classifyGraphqlRead(beaconGet));
  assert.doesNotThrow(() => classifyGraphqlRead(sse));
  assert.equal(classifyGraphqlRead(beacon), undefined);
  assert.equal(classifyGraphqlRead(beaconGet), undefined);
  assert.equal(classifyGraphqlRead(sse), undefined);
});

test('fails closed when the URL is not a /graphql endpoint', () => {
  assert.equal(classifyGraphqlRead({ method: 'POST', url: 'https://app.test/api', postData: JSON.stringify({ query: '{ me }' }) }), undefined);
});

test('endpoint is matched by PATHNAME, not a full-URL substring (query-string decoy fails closed)', () => {
  // Regression for the substring fail-open: a non-GraphQL URL carrying "/graphql" only in its query
  // string must NOT be admitted as a read (the same class as the urlMatchesTarget ?next= fail-open).
  const decoy = { method: 'POST', url: 'https://third.test/collect?next=/graphql', postData: JSON.stringify({ query: '{ me }' }) };
  assert.equal(classifyGraphqlRead(decoy), undefined);
  // A sibling path that merely shares the "graphql" string but is not a /graphql endpoint segment.
  assert.equal(
    classifyGraphqlRead({ method: 'POST', url: 'https://app.test/graphql-metrics', postData: JSON.stringify({ query: '{ me }' }) }),
    undefined,
  );
  // An unparseable / non-absolute URL fails closed rather than throwing.
  assert.equal(
    classifyGraphqlRead({ method: 'POST', url: '/graphql', postData: JSON.stringify({ query: '{ me }' }) }),
    undefined,
  );
});

test('admits a real nested /graphql endpoint (e.g. /api/graphql) — boundary match, not a substring fluke', () => {
  // The pathname boundary still admits a genuine endpoint at a sub-path.
  assert.equal(
    classifyGraphqlRead({ method: 'POST', url: 'https://app.test/api/graphql', postData: JSON.stringify({ query: '{ me }' }) }),
    'read',
  );
});

test('fails closed on empty or null postData', () => {
  assert.equal(classifyGraphqlRead({ method: 'POST', url: 'https://app.test/graphql', postData: '' }), undefined);
  assert.equal(classifyGraphqlRead({ method: 'POST', url: 'https://app.test/graphql', postData: null }), undefined);
});
