// enduser-handbook capture asset — non-normative reference implementation for the Playwright
// reference case. The normative, engine-agnostic contract lives in
// references/capture-spec-helpers.md (and capture-safety.md / page-identity.md).
// Engine-neutral: reused as-is by any engine's driver glue.
//
// capture-guard-policy.mjs — the PURE classifier decision for the capture guard, factored out of
// capture-helpers.playwright.ts so it is unit-testable without a browser and so the branch ORDER is
// asserted on real code, not just on comment line numbers. decideRoute({method,url,postData,
// resourceType}, opts) returns { action: 'allow' | 'block', reason }. The Playwright route handler
// just maps allow→continue, block→abort+record. The seven `// [guard:*]` sentinels live HERE, in
// order, so reordering the actual decisions is what the order test catches.
//
// This is DEFENSE-IN-DEPTH, not permission to click ambiguous controls — the human capture-safety
// classification still governs every click. The guard fails closed on anything it cannot prove is
// a read.

// Built-in dangerous-verb path matcher (finding 4). Even a GET can be destructive
// (/items/5/disable, /users/7/delete-now), and an author may forget to list it in denyPatterns.
// This is additive to the caller's denyPatterns and runs as part of the deny step, BEFORE any
// method-based allow. Matched against tokenized URL path segments so it is not fooled by casing or
// camel/snake/kebab joins.
const DANGEROUS_VERB_SET = new Set([
  // EN
  'delete',
  'destroy',
  'remove',
  'disable',
  'deactivate',
  'approve',
  'send',
  'finalize',
  'cancel',
  'revoke',
  'reset',
  'purge',
  'wipe',
  // DE — localized route segments are unusual but do occur; including them is harmless
  // defense-in-depth (the tokenizer keeps umlauts so these match).
  'löschen',
  'entfernen',
  'deaktivieren',
]);

/**
 * Split a string into normalized lowercase tokens across camelCase, snake_case, kebab-case, and
 * URL path/query separators. "deleteUser" → ['delete','user']; "/users/7/delete-now" →
 * ['users','7','delete','now']; "remove_item" → ['remove','item'].
 *
 * @param {string} value
 * @returns {string[]}
 */
export function tokenize(value) {
  if (!value) return [];
  return String(value)
    // split camelCase / PascalCase boundaries: insert a space between a lower/digit and an upper.
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    // any run of non-alphanumeric becomes a boundary. German umlauts/ß are KEPT as word chars so
    // "löschen" stays one token (an ASCII-only class would split it into "l"+"schen" and miss the
    // verb). Keep this aligned with control-inventory.mjs's tokenizer.
    .split(/[^A-Za-z0-9äöüßÄÖÜ]+/)
    .map((t) => t.toLowerCase())
    .filter((t) => t.length > 0);
}

// Percent-decode defensively and PER-RUN. decodeURIComponent on the whole string throws on a single
// malformed sequence (e.g. a lone "%" or "%zz") and would force an all-or-nothing fallback to the
// raw value — which hides a validly-encoded dangerous verb sitting next to a malformed escape
// ("/items/%64elete/%zz" → "delete" stays masked). We instead decode each maximal RUN of consecutive
// "%XX" escapes together (a run, not a single escape, so multi-byte UTF-8 like "%C3%B6" for "ö"
// decodes correctly).
//
// A run can still be all-valid-hex yet invalid UTF-8 ("%E0%A4%64" — "%64"='d' wedged inside a broken
// 3-byte lead): decodeURIComponent throws on the WHOLE run, and a plain `return run` would leave
// "%E0%A4%64elete" literal, masking "delete". So on throw we fall back to byte-level decode — turn the
// run's "%XX" pairs into raw bytes and UTF-8-decode NON-fatally: invalid byte sequences become U+FFFD
// (a token boundary) while valid ASCII bytes (the verb letters) survive and still tokenize.
function safeDecode(value) {
  return value.replace(/(?:%[0-9A-Fa-f]{2})+/g, (run) => {
    try {
      return decodeURIComponent(run);
    } catch {
      // The regex guarantees the run is whole "%XX" pairs, so length is a multiple of 3 and each pair
      // is valid hex — the byte build cannot NaN and TextDecoder({fatal:false}) cannot throw.
      const bytes = new Uint8Array(run.length / 3);
      for (let i = 0; i < bytes.length; i += 1) {
        bytes[i] = parseInt(run.slice(i * 3 + 1, i * 3 + 3), 16);
      }
      return new TextDecoder('utf-8', { fatal: false }).decode(bytes);
    }
  });
}

function hasDangerousVerbToken(value) {
  for (const token of tokenize(value)) {
    if (DANGEROUS_VERB_SET.has(token)) return true;
  }
  return false;
}

// Doubly (or triply...) percent-encoded verbs — "%2564elete" ("%25"→"%", leaving "%64elete") — must
// not slip past a single safeDecode pass. But scanning ONLY the fully-decoded fixed point is not
// enough either: iterative decoding can FUSE an already-plain verb sitting next to a stray escape into
// a longer word that no longer matches ("delete%2573" decodes to "deletes", not "delete"), even though
// the RAW string already contains the exact token "delete" (tokenize splits on "%"). So this checks
// the RAW value AND every intermediate decode pass — s0 (raw) through s5 (after 5 decode passes) — and
// flags a hit on ANY of them.
//
// Termination of the decode sequence is provable, not assumed: every pass that changes the string
// strictly SHORTENS it (each "%XX" escape, 3 chars, collapses to at most one UTF-16 code unit —
// including on the byte-level fallback path, where an invalid sequence collapses to a single U+FFFD),
// so length is a strictly decreasing natural number and a fixed point is reached in at most len/2
// passes. The cap of 5 is a defensive BOUND, not the termination argument — and it fails CLOSED: if
// decoding s5 once more would still change it (the cap was hit before a fixed point, encode depth >=
// 6), the input is REJECTED as dangerous rather than scanned in its still-partially-encoded state,
// which could be hiding the verb behind one more layer of encoding we chose not to peel.
function decodeSequenceHasDangerousVerb(value) {
  let current = value;
  for (let pass = 0; pass <= 5; pass += 1) {
    if (hasDangerousVerbToken(current)) return true;
    if (pass === 5) break;
    current = safeDecode(current);
  }
  return safeDecode(current) !== current;
}

/**
 * True when the URL path OR query contains a dangerous verb token. Uses the parsed URL's pathname +
 * search when parseable, else the raw string. The RAW value AND every intermediate percent-decode pass
 * are scanned (see decodeSequenceHasDangerousVerb) so "/items/%64elete", "/items/%2564elete"
 * (doubly-encoded), "/x/l%C3%B6schen", and "/items/delete%2573" (a plain verb fused with a stray
 * escape by iterative decoding) are all caught — a single-pass, undecoded, or fixed-point-only scan
 * would let some of these through. The query is scanned too so "/items?action=delete" is blocked.
 * Token-exact so "deletedAt" does not match (token is "deleted", not "delete") — the URL forms we
 * guard ("/x/delete", "/x/delete-now", "deleteUser", "?action=delete") all tokenize to a bare verb.
 *
 * NOTE (fail-closed by design): a legitimate page whose ROUTE itself contains a dangerous token —
 * "/settings/reset-password", "/account/delete" as a view — is blocked here too. That is correct
 * for a fail-closed guard: the author must add a `classifyRequest` that admits that specific
 * read-only navigation, rather than the guard guessing. There is deliberately no auto-exemption for
 * the first document GET (it would be a reusable bypass surface). This extends to the query string
 * too: an encoded verb token there (e.g. "?q=%2564elete") normalizes to the same fail-closed outcome
 * as the unencoded form. A search UI whose query legitimately carries such a token as literal search
 * text — not a route action — needs a `classifyRequest` that admits that specific read.
 *
 * @param {string} url
 * @returns {boolean}
 */
export function hasDangerousVerb(url) {
  let scanned;
  try {
    // new URL throws on a relative URL; fall back to the raw string in that case. The pathname and
    // search are concatenated RAW (before any decoding) — the inserted space is a literal separator,
    // never part of a "%XX" run, so decoding the combined string is equivalent to decoding each part
    // separately and is what lets a single decode-sequence scan cover both.
    const u = new URL(url);
    scanned = u.pathname + ' ' + u.search;
  } catch {
    scanned = url;
  }
  return decodeSequenceHasDangerousVerb(scanned);
}

/**
 * Match a request against caller deny patterns. Each pattern (a substring or a RegExp) is tested
 * against BOTH the URL AND the request body (postData), so an author can target a body-shaped write
 * the URL alone cannot identify — e.g. a GraphQL mutation POSTed to a generic /graphql endpoint, via
 * a denyPattern like /\bmutation\b/. This is the backstop for the case where classifyRequest wrongly
 * returns 'read' for such a body: deny runs first and still blocks it.
 *
 * The built-in dangerous-verb check (hasDangerousVerb) deliberately scans the URL path ONLY, never
 * arbitrary bodies — verb-scanning request bodies would false-positive on legitimate read payloads
 * that merely mention a verb. Body-shaped writes rely on classifyRequest failing closed, with these
 * user denyPatterns as the explicit backup.
 *
 * RegExp.test is STATEFUL when the regex carries the /g or /y flag (lastIndex advances between
 * calls), so we test against a freshly-rebuilt, flagless copy to keep matching deterministic.
 *
 * @param {{ url: string, postData?: string|null }} req
 * @param {Array<string|RegExp>} patterns
 * @returns {boolean}
 */
export function matchesDeny(req, patterns) {
  // Scan the URL and the body separately (not concatenated) so a pattern cannot accidentally match
  // across the url↔body boundary.
  const haystacks = [req.url, req.postData].filter((h) => typeof h === 'string');
  for (const p of patterns) {
    for (const haystack of haystacks) {
      if (typeof p === 'string') {
        if (haystack.includes(p)) return true;
      } else {
        // Rebuild without g/y so .test is stateless and order-independent.
        const safe = new RegExp(p.source, p.flags.replace(/[gy]/g, ''));
        if (safe.test(haystack)) return true;
      }
    }
  }
  return false;
}

/**
 * @typedef {{ method: string, url: string, postData: string|null, resourceType: string }} GuardRequest
 * @typedef {{ action: 'allow'|'block', reason: string }} GuardDecision
 * @typedef {{ denyPatterns?: Array<string|RegExp>, classifyRequest?: (req: GuardRequest) => ('read'|'benign'|undefined), allowBeacons?: boolean }} GuardPolicyOptions
 *
 * `classifyRequest` totality contract: the predicate is consulted ONCE per request (hoisted below
 * the deny step) and is now also reached for ping/beacon and eventsource requests — v1.0.5 returned
 * from the beacon branch BEFORE classify-read, so the predicate never saw a beacon. Because it is now
 * total over every classified request it MUST return `undefined` for any request it does not
 * recognize and MUST NOT throw; a throw escapes decideRoute (no fail-closed wrapper). 'read' ADMITS
 * (allow), 'benign' BLOCKS but is not counted dangerous, anything else (incl. a stray truthy) falls
 * through to the fail-closed default.
 */

/**
 * The ordered classifier. Returns allow/block + a reason. The branch order is the contract (SEVEN
 * sentinels): deny < classify-benign < eventsource < beacon < classify-read < get-head <
 * fail-closed. The built-in dangerous-verb check is part of the deny step so the sentinels keep their
 * order and counts. `classifyRequest` is hoisted to a single call after the deny step (see the
 * typedef's totality contract): deny + the built-in dangerous-verb block always win over it; then a
 * 'benign' verdict silences eventsource/beacon/fail-closed without flagging; a 'read' verdict admits
 * an SSE or a generic POST; everything else fails closed.
 *
 * @param {GuardRequest} req
 * @param {GuardPolicyOptions} [opts]
 * @returns {GuardDecision}
 */
export function decideRoute(req, opts = {}) {
  const { denyPatterns = [], classifyRequest, allowBeacons = false } = opts;
  const method = req.method;
  const resourceType = req.resourceType;

  // [guard:deny]
  // Caller deny patterns win over everything. The built-in dangerous-verb path block lives in this
  // same step (additive to denyPatterns) so a destructive GET cannot slip through [guard:get-head].
  if (matchesDeny(req, denyPatterns)) {
    return { action: 'block', reason: 'deny-pattern' };
  }
  if (hasDangerousVerb(req.url)) {
    return { action: 'block', reason: 'deny-dangerous-verb' };
  }

  // Hoist the classifier to a single call AFTER the deny step (deny must keep winning). The verdict
  // is read by classify-benign, eventsource, and classify-read below. Per the totality contract it
  // returns 'read' | 'benign' | undefined and never throws.
  const verdict = classifyRequest?.(req);

  // [guard:classify-benign]
  // Known-harmless dev telemetry (e.g. a console-log POST or a Sentry beacon): block it (it never
  // fires) but flag it benign so the guard does not count it dangerous. Wins over eventsource/beacon/
  // fail-closed, but NOT over deny above.
  if (verdict === 'benign') return { action: 'block', reason: 'classify-benign' };

  // [guard:eventsource]
  // SSE is a long-lived GET the generic allow would wrongly admit to a live external endpoint.
  if (resourceType === 'eventsource') {
    if (verdict === 'read') return { action: 'allow', reason: 'eventsource-read' };
    return { action: 'block', reason: 'eventsource' };
  }

  // [guard:beacon]
  // Analytics beacons block unless explicitly opted in; denyPatterns above still win over allowBeacons.
  if (resourceType === 'ping') {
    if (allowBeacons) return { action: 'allow', reason: 'beacon-allowed' };
    return { action: 'block', reason: 'beacon' };
  }

  // [guard:classify-read]
  // The single read escape hatch (e.g. a GraphQL query). ONLY the exact string 'read' admits.
  if (verdict === 'read') {
    return { action: 'allow', reason: 'classify-read' };
  }

  // [guard:get-head]
  // Plain reads (CDN/fonts + the first goto document render). Reached only after the deny step has
  // cleared dangerous-verb GETs.
  if (method === 'GET' || method === 'HEAD') {
    return { action: 'allow', reason: 'get-head' };
  }

  // [guard:fail-closed]
  // Everything else (unclassified POST/PUT/PATCH/DELETE, etc.) is blocked + recorded.
  return { action: 'block', reason: 'fail-closed' };
}
