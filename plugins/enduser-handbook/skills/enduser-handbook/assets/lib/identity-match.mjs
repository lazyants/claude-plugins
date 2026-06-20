// enduser-handbook capture asset — non-normative reference implementation for the Playwright
// reference case. The normative, engine-agnostic contract lives in
// references/capture-spec-helpers.md (and capture-safety.md / page-identity.md). Fork for other
// engines.
//
// identity-match.mjs — the pure URL-matching predicate used by assertIdentity/armApiWait for the
// route check and the API-readiness wait. Kept browser-agnostic so the boundary-matching rules are
// unit-testable (tests/identity-match.test.mjs) without Playwright. capture-helpers.playwright.ts
// imports urlMatchesTarget from here.

/**
 * Match a URL against a target by PATHNAME boundary — NOT a bare substring.
 *
 * A string target is compared against the URL's pathname: it matches on an EXACT pathname or a
 * SEGMENT-BOUNDARY prefix. This closes two identity fail-opens that a substring (`String.includes`)
 * check leaves open:
 *   - '/api/users' must NOT match '/api/users-old' (prefix without a segment boundary);
 *   - route '/settings/users' must NOT be satisfied by a redirect to '/login?next=/settings/users'
 *     (the target appears only in the query string, never in the pathname).
 *
 * A RegExp target is tested against the FULL URL as-is — the caller opts into fuzzy / cross-origin /
 * query-string matching explicitly. If the URL cannot be parsed (a bare relative string in a non-
 * browser context), we fall back to a substring test so the predicate still functions.
 *
 * @param {string} actualUrl  the observed URL (page URL, or a response URL)
 * @param {string|RegExp} target  the route/API the caller is asserting
 * @returns {boolean}
 */
export function urlMatchesTarget(actualUrl, target) {
  if (target instanceof RegExp) return target.test(actualUrl);

  // Fail CLOSED on a blank/absent target. Without this, `${''}/` === '/' and pathname.startsWith('/')
  // is true for EVERY URL, so an empty or missing route/waitForApi (a blank manifest field, a
  // templating bug) would silently certify ANY page — the exact identity fail-open this matcher
  // exists to prevent. Throw loudly at capture time instead of matching everything.
  if (typeof target !== 'string' || target.trim() === '') {
    throw new Error(
      'urlMatchesTarget: empty/blank route or API target — refusing to certify identity against a blank target.',
    );
  }

  let pathname;
  try {
    pathname = new URL(actualUrl).pathname;
  } catch {
    // Unparseable (e.g. a relative URL with no base) — degrade to substring rather than throw.
    return String(actualUrl).includes(target);
  }

  if (pathname === target) return true;
  // Segment-boundary prefix: '/items' matches '/items/5' but not '/items-archive'.
  const prefix = target.endsWith('/') ? target : `${target}/`;
  // A root target '/' yields prefix '/', which startsWith() matches for EVERY pathname — that would
  // re-introduce the match-everything fail-open. Root only matches the exact root pathname (handled by
  // the `pathname === target` check above); a non-root path must NOT be certified as the root chapter.
  if (prefix === '/') return false;
  return pathname.startsWith(prefix);
}
