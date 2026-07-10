// enduser-handbook capture asset — non-normative reference implementation for the Playwright
// reference case. The normative, engine-agnostic contract lives in
// references/capture-spec-helpers.md (and capture-safety.md / page-identity.md).
// Engine-neutral: reused as-is by any engine's driver glue.
//
// graphql-read-classifier.mjs — the PURE GraphQL read-classifier, factored out of
// capture.example.spec.ts so it is unit-testable without a browser. This is the capture guard's
// SINGLE allow escape-hatch: it is the one place that can turn an otherwise fail-closed POST into a
// 'read' (see decideRoute's [guard:classify-read] step in capture-guard-policy.mjs). It admits ONLY
// a single, inline, unambiguous READ document and fails closed (returns undefined) on anything else.
//
// This is DEFENSE-IN-DEPTH, not permission to click ambiguous controls — the human capture-safety
// classification still governs every click, and the caller's denyPatterns still run BEFORE this.

/**
 * True when the URL's PATHNAME is a GraphQL endpoint — exactly `/graphql` or any `/…/graphql`
 * (e.g. `/api/graphql`). Checks the PATHNAME, never a full-URL substring: a substring test that matches
 * the path fragment anywhere in the URL admits a decoy like `https://third.test/collect?next=/graphql`
 * (the fragment lives in the query string and the host is not even ours), punching a hole in the
 * guard's fail-closed contract.
 * `endsWith('/graphql')` guarantees a `/` boundary, so `/graphql-metrics` and `/notgraphql` are
 * rejected. Origin/external scoping is NOT this function's job — the guard's denyPatterns run BEFORE
 * this and are where an author blocks specific external hosts (the guard allows reads broadly, like
 * cross-origin GET fonts/CDN; it is a write/send blocker, not an origin firewall).
 *
 * @param {string} url
 * @returns {boolean}
 */
function isGraphqlEndpoint(url) {
  let pathname;
  try {
    pathname = new URL(url).pathname;
  } catch {
    return false; // unparseable / non-absolute URL → not a recognized endpoint → fail closed
  }
  return pathname === '/graphql' || pathname.endsWith('/graphql');
}

/**
 * Classify a GraphQL request as a safe read. Returns 'read' ONLY for a single, inline, unambiguous
 * read document; returns undefined (fail closed) for everything else. The rules, in order:
 *   - must be a POST to a /graphql ENDPOINT (matched by pathname, see isGraphqlEndpoint) with an
 *     inline string body (a PUT/PATCH/DELETE, or a persisted op carrying only a queryId / no inline
 *     body, is never a read);
 *   - the document must define NO `mutation`/`subscription` operation anywhere — a mixed
 *     query+mutation doc fails closed even though it also contains a query. The `\b…\b` word-boundary
 *     means a FIELD merely named `mutationLog`/`subscriptionFeed` does NOT trip this (no boundary
 *     between the camelCase join), so `query { mutationLog }` is correctly admitted as a read;
 *   - exactly one top-level operation — a second `query` keyword means multiple named ops, which are
 *     ambiguous → fail closed;
 *   - after stripping leading whitespace and `# …` comment lines, the FIRST significant token must be
 *     the keyword `query` (named/explicit query) OR `{` (anonymous shorthand query). A doc that
 *     starts with anything else — `fragment …`, a bare type/word, junk, empty — fails closed.
 *
 * @param {{ method: string, url: string, postData: string | null }} req
 * @returns {'read'|undefined}
 */
export function classifyGraphqlRead(req) {
  if (req.method !== 'POST' || !isGraphqlEndpoint(req.url) || !req.postData) return undefined;
  let op;
  try {
    op = JSON.parse(req.postData)?.query;
  } catch {
    return undefined; // unparseable body → fail closed
  }
  if (typeof op !== 'string') return undefined; // persisted / non-inline op → fail closed

  // No mutation/subscription operation anywhere in the document.
  if (/\b(mutation|subscription)\b/.test(op)) return undefined;
  // At most one top-level operation (a second `query` keyword means multiple named ops).
  if ((op.match(/\bquery\b/g) || []).length > 1) return undefined;

  // Strip leading whitespace and any leading run of `# …` comment lines, then inspect the first
  // significant token.
  const head = op.replace(/^(?:\s|#[^\n]*\n?)*/, '');
  // First significant token: `query` keyword, or `{` shorthand.
  if (/^query\b/.test(head) || head.startsWith('{')) return 'read';
  return undefined; // fragment-only / arbitrary / empty doc → fail closed
}
