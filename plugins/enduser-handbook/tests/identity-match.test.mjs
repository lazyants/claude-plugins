// Unit tests for the pure URL-identity matcher. Zero deps — runs under Node's built-in test runner:
// `node --test identity-match.test.mjs`.
//
// These pin the two identity fail-opens a substring (String.includes) check leaves open: a prefix
// without a segment boundary ('/api/users' vs '/api/users-old') and a target that appears only in a
// query-string redirect ('/login?next=/settings/users' vs route '/settings/users').

import test from 'node:test';
import assert from 'node:assert/strict';

import { urlMatchesTarget } from '../skills/enduser-handbook/assets/lib/identity-match.mjs';

test('exact pathname matches', () => {
  assert.equal(urlMatchesTarget('https://app.test/settings/users', '/settings/users'), true);
  // Query string on the actual URL does not affect the pathname match.
  assert.equal(urlMatchesTarget('https://app.test/api/users?page=2', '/api/users'), true);
});

test('segment-boundary prefix matches, bare prefix does NOT', () => {
  // A detail route under the section counts.
  assert.equal(urlMatchesTarget('https://app.test/items/5', '/items'), true);
  // But a sibling that merely shares a string prefix must NOT.
  assert.equal(urlMatchesTarget('https://app.test/items-archive', '/items'), false);
  assert.equal(urlMatchesTarget('https://app.test/api/users-old', '/api/users'), false);
});

test('a target appearing only in the query string does NOT certify identity (redirect fail-open)', () => {
  // The exact reviewer case: a redirect to /login carrying the wanted route as ?next=.
  assert.equal(
    urlMatchesTarget('https://app.test/login?next=/settings/users', '/settings/users'),
    false,
  );
});

test('a RegExp target is tested against the FULL url (caller opts into fuzzy matching)', () => {
  assert.equal(urlMatchesTarget('https://app.test/api/users?page=2', /\/api\/users\b/), true);
  assert.equal(urlMatchesTarget('https://app.test/login?next=/x', /next=\/x/), true);
  assert.equal(urlMatchesTarget('https://app.test/other', /\/api\/users\b/), false);
});

test('an unparseable URL degrades to a substring test rather than throwing', () => {
  assert.equal(urlMatchesTarget('/relative/settings/users', '/settings/users'), true);
});

test('a blank / whitespace / missing target FAILS CLOSED (throws), never matching every URL', () => {
  // Regression for the identity fail-open where `${""}/` === "/" made startsWith("/") true for ANY
  // URL — a blank route/waitForApi must refuse to certify identity, loudly.
  assert.throws(() => urlMatchesTarget('https://app.test/anything', ''));
  assert.throws(() => urlMatchesTarget('https://app.test/', '   '));
  assert.throws(() => urlMatchesTarget('https://app.test/x', undefined));
});

test('root target "/" matches ONLY the exact root, not every path', () => {
  // Regression for the '/' fail-open: prefix "/" made startsWith("/") true for every pathname.
  assert.equal(urlMatchesTarget('https://app.test/', '/'), true);
  assert.equal(urlMatchesTarget('https://app.test', '/'), true); // pathname normalizes to "/"
  assert.equal(urlMatchesTarget('https://app.test/login', '/'), false);
  assert.equal(urlMatchesTarget('https://app.test/settings/users', '/'), false);
});
