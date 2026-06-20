// enduser-handbook capture asset — non-normative reference implementation for the Playwright
// reference case. The normative, engine-agnostic contract lives in
// references/capture-spec-helpers.md (and capture-safety.md / page-identity.md). Fork for other
// engines.
//
// graphql-read-classifier.d.mts — TypeScript declarations for graphql-read-classifier.mjs so a
// downstream typechecking project resolves the .ts → .mjs import. This repo does not compile
// TypeScript.

/**
 * The capture guard's single allow escape-hatch. Returns 'read' ONLY for a single, inline,
 * unambiguous GraphQL read document; returns undefined (fail closed) for everything else.
 */
export function classifyGraphqlRead(req: {
  method: string;
  url: string;
  postData: string | null;
}): 'read' | undefined;
