// enduser-handbook capture asset — non-normative reference implementation for the Playwright
// reference case. The normative, engine-agnostic contract lives in
// references/capture-spec-helpers.md (and capture-safety.md / page-identity.md).
// Engine-neutral: reused as-is by any engine's driver glue.
//
// identity-match.d.mts — TypeScript declarations for identity-match.mjs so a downstream typechecking
// project resolves the .ts → .mjs import. This repo does not compile TypeScript.

/**
 * Match a URL against a target by pathname boundary (exact or segment-prefix) for a string target,
 * or by full-URL RegExp test for a RegExp target. Falls back to substring only when the URL is
 * unparseable. Closes the substring identity fail-opens (/api/users vs /api/users-old; a route
 * satisfied only via a ?next= query redirect).
 */
export function urlMatchesTarget(actualUrl: string, target: string | RegExp): boolean;
