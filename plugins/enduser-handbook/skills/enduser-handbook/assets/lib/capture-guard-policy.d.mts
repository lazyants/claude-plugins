// enduser-handbook capture asset — non-normative reference implementation for the Playwright
// reference case. The normative, engine-agnostic contract lives in
// references/capture-spec-helpers.md (and capture-safety.md / page-identity.md).
// Engine-neutral: reused as-is by any engine's driver glue.
//
// capture-guard-policy.d.mts — TypeScript declarations for capture-guard-policy.mjs so a downstream
// typechecking project resolves the .ts → .mjs import. This repo does not compile TypeScript.

/** A request as the guard policy sees it. */
export interface GuardRequest {
  method: string;
  url: string;
  postData: string | null;
  resourceType: string;
  /**
   * The previous hop's URL when this request is a REDIRECT HOP; absent/null for a browser-originated
   * request. No decideRoute branch reads it — it is passed through so a project's classifyRequest can
   * tell a hop from a fresh request. Admitting the ORIGIN never admits its hop.
   */
  redirectedFrom?: string | null;
}

/** The classifier decision. */
export interface GuardDecision {
  action: 'allow' | 'block';
  reason: string;
}

/**
 * The audit result for a redirect hop. A VERDICT, not an action: the hop has already been sent when
 * it becomes observable, so there is nothing left to allow or block. `reason` is decideRoute's reason
 * prefixed with 'redirect-hop:'.
 */
export interface RedirectHopAudit {
  verdict: 'clean' | 'dangerous' | 'benign';
  reason: string;
}

/** Options for decideRoute. classifyRequest's 'read' admits an otherwise-blocked read; 'benign' blocks-uncounted. */
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
 * fail-closed. Returns allow/block + a reason. Fails closed on any NON-GET/HEAD request not proven a
 * read; a GET/HEAD that reaches the get-head step — past every block listed before it — is admitted
 * unconditionally (issue #470).
 */
export function decideRoute(req: GuardRequest, opts?: GuardPolicyOptions): GuardDecision;

/**
 * Audit a redirect hop with the same ordered policy, on the hop's OWN method and URL. DETECTION, NOT
 * PREVENTION — a hop never reaches the route handler (issue #471) and has already been sent by the
 * time it is observable, so this reports a verdict the caller records; it cannot block anything.
 */
export function auditRedirectHop(req: GuardRequest, opts?: GuardPolicyOptions): RedirectHopAudit;
