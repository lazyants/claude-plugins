// enduser-handbook capture asset — non-normative reference implementation for the Playwright
// reference case. The normative, engine-agnostic contract lives in
// references/capture-spec-helpers.md (and capture-safety.md / page-identity.md). Fork for other
// engines.
//
// capture-guard-policy.d.mts — TypeScript declarations for capture-guard-policy.mjs so a downstream
// typechecking project resolves the .ts → .mjs import. This repo does not compile TypeScript.

/** A request as the guard policy sees it. */
export interface GuardRequest {
  method: string;
  url: string;
  postData: string | null;
  resourceType: string;
}

/** The classifier decision. */
export interface GuardDecision {
  action: 'allow' | 'block';
  reason: string;
}

/** Options for decideRoute. Only classifyRequest can admit an otherwise-blocked read. */
export interface GuardPolicyOptions {
  denyPatterns?: Array<string | RegExp>;
  /**
   * The single read/benign escape. 'read' ADMITS (allows) an otherwise-blocked read; 'benign' BLOCKS
   * the request but excludes it from the dangerous ledger; anything else (incl. undefined) fails
   * closed. Now consulted for ping/beacon and eventsource requests too, so it MUST be total — return
   * `undefined` for any request it does not recognize and never throw.
   */
  classifyRequest?: (req: GuardRequest) => 'read' | 'benign' | undefined;
  allowBeacons?: boolean;
}

/** Split a string into normalized lowercase tokens across camel/snake/kebab/URL boundaries. */
export function tokenize(value: string): string[];

/** True when the URL path contains a dangerous-verb token (delete/destroy/remove/…). */
export function hasDangerousVerb(url: string): boolean;

/**
 * True when the request URL OR body (postData) matches any caller deny pattern (string substring or
 * stateless RegExp). Scanning the body lets an author deny a body-shaped write (e.g. a GraphQL
 * mutation POSTed to a generic /graphql) that the URL alone cannot identify.
 */
export function matchesDeny(
  req: { url: string; postData?: string | null },
  patterns: Array<string | RegExp>,
): boolean;

/**
 * Ordered classifier: deny < classify-benign < eventsource < beacon < classify-read < get-head <
 * fail-closed. Returns allow/block + a reason. Fails closed on anything not proven a read.
 */
export function decideRoute(req: GuardRequest, opts?: GuardPolicyOptions): GuardDecision;
