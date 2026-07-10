// enduser-handbook capture asset — non-normative reference implementation for the Playwright
// reference case. The normative, engine-agnostic contract lives in
// references/capture-spec-helpers.md (and capture-safety.md / page-identity.md).
// Reimplement this driver glue for another engine; the engine-neutral lib/*.mjs helpers are
// reused as-is.
//
// capture-helpers.playwright.ts — the safety / identity / masking machinery as importable
// functions, mapping 1:1 to the prose rules in capture-safety.md and page-identity.md. This is
// DEFENSE-IN-DEPTH, not permission to click ambiguous controls: the human capture-safety
// classification still governs every click. The guard exists to fail-closed if a live request
// fires anyway (a mis-classified control, a hook, a beacon), not to make clicking safe.
//
// Auth is assumed via a pre-seeded storageState, NOT a live login — a live OAuth POST is a write.
//
// MODULE MINIMUM: this file requires Playwright >= 1.51, because assertIdentity's spinner check
// uses `locator.filter({ visible: true })`, which was added in Playwright 1.51 (the separate
// routeWebSocket floor noted below is 1.48 and is unaffected by this higher module-wide minimum).

import type { Browser, BrowserContext, Locator, Page, Route, Request } from '@playwright/test';
// The ordered classifier lives in a pure, browser-agnostic module so its branch ORDER (deny <
// classify-benign < eventsource < beacon < classify-read < get-head < fail-closed) is unit-testable,
// not just grep-able. The seven `// [guard:*]` sentinels live in decideRoute. This handler only maps
// the decision onto abort/continue + recording.
import { decideRoute, tokenize } from './lib/capture-guard-policy.mjs';
import type { GuardRequest } from './lib/capture-guard-policy.mjs';
// Pure, unit-tested URL-identity matcher (pathname-boundary, not substring) so route/API matching
// cannot be fooled by '/api/users-old' or a '?next=/settings/users' redirect.
import { urlMatchesTarget } from './lib/identity-match.mjs';

/** A request as seen by classifyRequest. 'read' admits; 'benign' blocks-uncounted; anything else falls through. */
export type ClassifiedRequest = GuardRequest;

export interface CaptureGuardOptions {
  /** URL substrings/regexes that must ALWAYS be blocked + recorded, regardless of method. */
  denyPatterns: Array<string | RegExp>;
  /**
   * The single read/benign escape hatch — two non-undefined verdicts, no built-in defaults (the
   * author supplies the predicate):
   *   - return `'read'` to ADMIT an otherwise-blocked request (e.g. a GraphQL query — inspect
   *     postData for `query` vs `mutation`);
   *   - return `'benign'` to BLOCK a known-harmless dev-telemetry request (a console-log POST, a
   *     Sentry beacon) WITHOUT counting it dangerous — it never fires, but `assertNoDangerousHits()`
   *     will not false-trip on it (it lands in the separate `blockedBenign` ledger);
   *   - anything else (including `undefined`) is non-read/non-benign and falls through to fail-closed.
   *
   * Totality contract: the predicate is now consulted for ping/beacon and eventsource requests too
   * (the v1.0.5 beacon branch returned before it could run), so it MUST return `undefined` for any
   * request it does not recognize and MUST NOT throw.
   */
  classifyRequest?: (req: ClassifiedRequest) => 'read' | 'benign' | undefined;
  /** Exceptional opt-in for analytics beacons. denyPatterns still win over it. */
  allowBeacons?: boolean;
}

export interface CaptureGuard {
  /**
   * Throws if any dangerous/blocked request was recorded during capture. First runs a BOUNDED
   * best-effort drain: it waits until no new request has been recorded for `quietMs` (resetting the
   * timer each time one arrives), giving a delayed beacon/fetch/WebSocket fired after the last
   * interaction time to reach the handler — capped at `maxMs` so it cannot hang. This is best-effort,
   * NOT a guarantee that every late request has settled; a request that fires after the drain window
   * is not detected. Call it in a `finally`, before closing the context. It gates on the
   * `dangerousHits` ledger ONLY — requests classified `'benign'` are excluded by construction.
   */
  assertNoDangerousHits(quietMs?: number, maxMs?: number): Promise<void>;
  /**
   * Read-only snapshot of the requests blocked as `'benign'` (the classify-benign verdict) during
   * capture — for observability/debugging. These are NOT counted dangerous and never block
   * `assertNoDangerousHits()`. Returns a copy.
   */
  blockedBenign(): string[];
}

/**
 * Install the capture guard at BROWSER-CONTEXT level, BEFORE any page is created. The caller MUST
 * create the context with `serviceWorkers: 'block'` so service-worker traffic cannot slip past
 * context.route (page.route would miss it).
 *
 * HTTP is routed via context.route('**\/*'); each request is classified by the pure decideRoute
 * policy (deny < classify-benign < eventsource < beacon < classify-read < get-head < fail-closed).
 * denyPatterns and the built-in dangerous-verb path block win over everything; the default is
 * fail-closed on unclassified non-GET (and on dangerous-verb GETs).
 *
 * WebSockets are routed via context.routeWebSocket (Playwright >= 1.48) so a socket is blocked
 * WITHOUT connecting. If routeWebSocket is unavailable we THROW at install time rather than fall
 * back to page.on('websocket'), which can only OBSERVE an already-open socket and cannot block.
 */
export async function installCaptureGuard(
  context: BrowserContext,
  { denyPatterns, classifyRequest, allowBeacons = false }: CaptureGuardOptions,
): Promise<CaptureGuard> {
  const dangerousHits: string[] = [];
  // Requests blocked as known-harmless dev telemetry (the classify-benign verdict). Kept in a
  // SEPARATE ledger so they are aborted (they never fire) but do NOT count toward
  // assertNoDangerousHits, and do NOT bump lastHitAt — a chatty telemetry page must not stretch the
  // drain window.
  const blockedBenign: string[] = [];
  // Bumped on every recorded DANGEROUS hit (HTTP or WebSocket) so the drain loop below can detect "a
  // new hit arrived during the quiet window" and reset its timer. Benign blocks do not bump it.
  let lastHitAt = 0;

  const record = (route: Route, req: Request, reason: string): Promise<void> => {
    if (reason === 'classify-benign') {
      // Block it (it never fires) but do not count it dangerous and do not stretch the drain window.
      blockedBenign.push(`${reason}: ${req.method()} ${req.url()}`);
      return route.abort();
    }
    dangerousHits.push(`${reason}: ${req.method()} ${req.url()}`);
    lastHitAt = Date.now();
    return route.abort();
  };

  await context.route('**/*', (route: Route, req: Request) => {
    const classified: ClassifiedRequest = {
      method: req.method(),
      url: req.url(),
      postData: req.postData(),
      resourceType: req.resourceType(),
    };

    const decision = decideRoute(classified, { denyPatterns, classifyRequest, allowBeacons });
    if (decision.action === 'allow') {
      return route.continue();
    }
    return record(route, req, decision.reason);
  });

  // WebSockets: routeWebSocket blocks/records without connecting. No silent fallback.
  if (typeof (context as { routeWebSocket?: unknown }).routeWebSocket !== 'function') {
    throw new Error(
      'installCaptureGuard requires context.routeWebSocket (Playwright >= 1.48) to block ' +
        'WebSocket traffic without connecting. Upgrade Playwright to >= 1.48, or disable ' +
        'WebSockets in the captured build — do not fall back to page.on("websocket"), which ' +
        'can only observe an already-open socket and cannot block it.',
    );
  }
  await context.routeWebSocket('**/*', (ws) => {
    // Never connect upstream. Record the attempt and leave the socket unconnected.
    dangerousHits.push(`websocket: ${ws.url()}`);
    lastHitAt = Date.now();
  });

  return {
    async assertNoDangerousHits(quietMs = 500, maxMs = 5000) {
      // Bounded best-effort drain: wait until no new hit has been recorded for `quietMs`, resetting
      // the window each time one arrives, capped at `maxMs`. This gives a delayed beacon/fetch/
      // WebSocket fired after the last interaction time to reach the handler. It is best-effort — a
      // request that fires after the window closes is not detected.
      const deadline = Date.now() + maxMs;
      // Treat "now" as the last activity baseline so an idle guard waits a single quiet window.
      lastHitAt = lastHitAt || Date.now();
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const sinceLastHit = Date.now() - lastHitAt;
        if (sinceLastHit >= quietMs || Date.now() >= deadline) break;
        const wait = Math.min(quietMs - sinceLastHit, deadline - Date.now());
        await new Promise((resolve) => setTimeout(resolve, Math.max(wait, 0)));
      }
      if (dangerousHits.length > 0) {
        throw new Error(
          `Capture guard recorded ${dangerousHits.length} dangerous/blocked request(s) during ` +
            `capture — a live action may have fired:\n  ${dangerousHits.join('\n  ')}`,
        );
      }
    },
    blockedBenign: () => [...blockedBenign],
  };
}

export interface IdentityOptions {
  /** The route the page must be on. */
  route: string | RegExp;
  /** The primary heading text the chapter is about (server-rendered identity path). */
  heading?: string;
  /** Optional XHR/response URL to await (client-rendered identity path). */
  waitForApi?: string | RegExp;
  /**
   * A response wait armed with armApiWait() BEFORE navigation. Pass this instead of waitForApi when
   * the caller controls the navigation, to avoid the race where a fast client-rendered response
   * resolves before a wait registered after goto() attaches and is missed.
   */
  apiReady?: Promise<unknown>;
  /**
   * A state-variant marker for a page whose normal heading may be absent (an empty/error/denied
   * screen). `present` is a first-class READINESS + identity anchor — waited visible EARLY, before
   * the heading assertion — so a variant whose data-load returns a non-2xx (which armApiWait never
   * accepts) and whose normal heading never renders still has a valid readiness path. `absent`
   * asserts the WRONG-state marker is NOT visible, so a reverted precondition (e.g. the empty state
   * was restored to populated before a re-capture) fails the run instead of silently shipping the
   * wrong-but-real state. Both are matched with { exact: true }, mirroring dismissModal's identity
   * check (:422). See references/state-variants.md.
   */
  state?: { present?: string; absent?: string };
}

/**
 * Arm a response wait BEFORE navigation. A client-rendered API response can resolve in the window
 * between page.goto() returning and a wait registered afterwards attaching — that response would be
 * missed. Build the wait with this BEFORE goto(), then pass it to assertIdentity as `apiReady`.
 *
 * Only a SUCCESSFUL (res.ok(), i.e. 2xx) matching response certifies readiness. A matching 4xx/5xx is
 * NOT accepted — a fast error response must never satisfy identity; ignoring it lets the wait keep
 * looking and, if only errors ever return, time out loudly rather than certify a broken page.
 */
export function armApiWait(page: Page, waitForApi: string | RegExp): Promise<unknown> {
  // Pathname-boundary match (not substring) so '/api/users' is not satisfied by '/api/users-old',
  // AND only a successful (res.ok(), 2xx) response certifies readiness.
  return page.waitForResponse((res) => urlMatchesTarget(res.url(), waitForApi) && res.ok());
}

/**
 * Assert page identity before a shot. Readiness is established by the API wait when one is given (a
 * pre-armed apiReady, else a post-goto waitForApi). That API wait does NOT replace the heading
 * assertion: whenever a heading is supplied it is asserted additively, so an API-satisfied-but-DOM-
 * broken page (the response returned but the view never painted) cannot certify identity on the API
 * signal alone. Server-rendered pages use the heading as their sole signal. A state-variant marker
 * (`state.present`) is an equivalent, third readiness+identity signal for a screen whose normal
 * heading may be absent (empty/error/denied) — see references/state-variants.md. Always assert the
 * route and that no spinner remains. Fail loudly — a wrong screenshot is worse than no screenshot.
 */
export async function assertIdentity(
  page: Page,
  { route, heading, waitForApi, apiReady, state }: IdentityOptions,
): Promise<void> {
  if (apiReady !== undefined) {
    // Pre-armed before navigation — no post-goto race.
    await apiReady;
  } else if (waitForApi !== undefined) {
    // Legacy post-goto path (caller did not pre-arm): same predicate as armApiWait, registered now.
    await armApiWait(page, waitForApi);
  } else if (heading === undefined && state?.present === undefined) {
    throw new Error(
      'assertIdentity needs apiReady, waitForApi (client-rendered), heading (server-rendered), or ' +
        'state.present (a state-variant marker for an empty/error/denied screen).',
    );
  }

  // A state-variant marker (present) is a first-class READINESS + identity anchor, waited visible
  // EARLY — before the heading assertion — so an empty/error/denied screen whose normal heading is
  // absent, and whose data-load may return a non-2xx (which armApiWait never accepts), still has a
  // valid readiness signal. { exact: true } mirrors dismissModal's identity check (:422) so a
  // substring/case-insensitive match cannot false-trip. See references/state-variants.md.
  if (state?.present !== undefined) {
    await page.getByText(state.present, { exact: true }).first().waitFor({ state: 'visible' });
  }

  // Whenever a heading is supplied, assert the DOM actually rendered it — additively, regardless of
  // whether an API wait already ran. This is the server-rendered path's primary signal AND the guard
  // that a client-rendered page's API response did not certify a still-unpainted/error view.
  if (heading !== undefined) {
    await page.getByRole('heading', { name: heading }).waitFor({ state: 'visible' });
  }

  // Route check — pathname-boundary match (not substring), so a redirect to
  // '/login?next=/settings/users' does NOT satisfy route '/settings/users'.
  const url = page.url();
  if (!urlMatchesTarget(url, route)) {
    throw new Error(`Page identity failed: expected route ${String(route)}, got ${url}`);
  }

  // A state-variant WRONG-state marker must be absent — a reverted staged precondition (e.g. the
  // empty state was restored to populated before a re-capture) has the same route and may still lack
  // a heading momentarily, so it would otherwise pass identity and silently ship the wrong-but-real
  // state (the staged-state rule in page-identity.md). { exact: true } as above.
  if (state?.absent !== undefined) {
    const wrongStateVisible = await page.getByText(state.absent, { exact: true }).filter({ visible: true }).count();
    if (wrongStateVisible > 0) {
      throw new Error(
        `Page identity failed: wrong-state marker "${state.absent}" is unexpectedly visible on ${url}.`,
      );
    }
  }

  // No spinner/skeleton/loading overlay covering the capture area. Assert that NONE of the union
  // matches are visible — checking only `.first()` lets a hidden decoy earlier in DOM order mask a
  // later VISIBLE spinner. A count() on the visible-filtered locator is safe; we do NOT swallow a
  // real locator error into "no spinner" (no fail-open .catch), so a broken selector surfaces loudly.
  const spinners = page.locator('[aria-busy="true"], .spinner, .skeleton, [role="progressbar"]');
  const visibleSpinners = await spinners.filter({ visible: true }).count();
  if (visibleSpinners > 0) {
    throw new Error(`Page identity failed: a loading indicator is still visible on ${url}`);
  }
}

/**
 * Element-scoped screenshot. Use for a single component/region.
 *
 * With `{ maxHeight }`, guards against a RUNAWAY-height element (a layout bug ballooning a modal to
 * tens of thousands of px) by temporarily clamping the element's rendered height before the shot:
 * when the element is taller than `maxHeight`, its OWN `scrollTop` is reset to 0 and a temporary
 * inline `max-height`/`overflow: hidden` is applied, the shot is taken at `scale: 'css'` (DPR-neutral,
 * so a smoke-check on image height ≈ `maxHeight` holds in CSS pixels), then the prior inline `style`
 * attribute + `scrollTop` are RESTORED EXACTLY (the whole attribute is rewritten — preserving
 * `!important` priorities and `overflow-x/y` shorthands — or removed if there was none) in a `finally`.
 * This is a CSS height clamp, NOT a
 * viewport clip — it is viewport-independent (a `page.screenshot({ clip })` path breaks when
 * `maxHeight` exceeds the viewport height or the element is not top-aligned). The `scrollTop = 0`
 * reset is load-bearing: `locator.screenshot()` of an already-scrolled container captures its CURRENT
 * offset, not the top.
 *
 * CAVEATS: (a) the clamp shows ONLY the top `maxHeight` of the element — content below is hidden, so a
 * legitimately long region must be paginated (capture in sections) or its truncation disclosed; this
 * is a runaway-height guard, not a tall-capture solution. (b) Only the clamped element's OWN scroll is
 * reset; a deeply-nested INNER scroll container can still render at its own offset (out of scope here).
 * When `maxHeight` is omitted or the element is shorter, the plain element screenshot is used.
 */
export async function captureRegion(
  locator: Locator,
  path: string,
  opts: { maxHeight?: number } = {},
): Promise<void> {
  await locator.waitFor({ state: 'visible' });

  const { maxHeight } = opts;
  if (maxHeight !== undefined) {
    const height = await locator.evaluate((el) => el.getBoundingClientRect().height);
    if (height > maxHeight) {
      // Save the FULL inline `style` attribute + scrollTop, then clamp from the top, shoot, and ALWAYS
      // restore. Saving the whole attribute (not individual `el.style.*` shorthands) makes the restore
      // EXACT: reassigning `el.style.maxHeight`/`el.style.overflow` would drop an `!important` priority
      // and clobber a pre-existing `overflow-x`-only inline style (the shorthand reads ''), corrupting a
      // later capture of the same element. `setProperty` MERGES the clamp into any existing inline style.
      const saved = await locator.evaluate(
        (el, h) => {
          const target = el as HTMLElement;
          const prior = { styleAttr: target.getAttribute('style'), scrollTop: target.scrollTop };
          target.scrollTop = 0;
          target.style.setProperty('max-height', `${h}px`);
          target.style.setProperty('overflow', 'hidden');
          return prior;
        },
        maxHeight,
      );
      try {
        // scale: 'css' keeps the produced image at 1:1 CSS pixels (DPR-neutral smoke-check).
        await locator.screenshot({ path, scale: 'css' });
      } finally {
        await locator.evaluate(
          (el, prior) => {
            const target = el as HTMLElement;
            // Exact restore: rewrite the whole inline style attribute, or remove it if there was none.
            if (prior.styleAttr === null) target.removeAttribute('style');
            else target.setAttribute('style', prior.styleAttr);
            target.scrollTop = prior.scrollTop;
          },
          saved,
        );
      }
      return;
    }
  }
  await locator.screenshot({ path });
}

/** Viewport screenshot — use for long unpaginated lists where the full element overflows the frame. */
export async function captureViewport(page: Page, path: string): Promise<void> {
  await page.screenshot({ path });
}

/**
 * Open a modal dialog by clicking its trigger, then assert the dialog is actually open. Returns the
 * dialog locator (the opaque inner dialog box) for downstream capture/mask.
 */
export async function openModalDialog(page: Page, trigger: Locator): Promise<Locator> {
  await trigger.waitFor({ state: 'visible' });
  await trigger.click();
  const dialog = page.getByRole('dialog');
  await dialog.waitFor({ state: 'visible' });
  return dialog;
}

export interface DismissOptions {
  /** The dialog to dismiss (the locator returned by openModalDialog). */
  dialog: Locator;
  /** Verbatim identifying text the dialog must show — asserted BEFORE any dismiss action. */
  expectedText: string;
  /** Optional verbatim cancel/negative button label (fallback when Escape does not close). */
  cancelLabel?: string;
  /**
   * Project-specific safe-negative labels to allow in addition to the built-in defaults (e.g. a
   * localized "Verwerfen"). The cancelLabel must match one of these (built-ins ∪ safeLabels)
   * exactly; a label that merely contains a commit/destructive verb is still refused.
   */
  safeLabels?: string[];
}

// Safe-negative dismiss labels are an ALLOWLIST, not a denylist. A denylist of dangerous verbs is
// the wrong tool here: "Cancel" is simultaneously the most common SAFE negative AND a substring of
// destructive labels ("Cancel subscription"), so it cannot be classified by verb alone. Instead the
// caller's cancelLabel must EXACTLY equal one of a small set of known-safe negatives (the default
// set below, extensible per project via DismissOptions.safeLabels). Anything else — including a
// label that merely contains a commit/destructive verb — is refused. Tokenize is still used to
// reject a multi-word label whose first token is a commit/destructive verb even if the author added
// it to safeLabels by mistake.
const DEFAULT_SAFE_LABELS = [
  // EN
  'Cancel',
  'No',
  'Close',
  'Back',
  'Go back',
  'Keep',
  'Dismiss',
  'Not now',
  // DE
  'Abbrechen',
  'Nein',
  'Schließen',
  'Zurück',
  'Behalten',
];
// Verbs a SAFE negative label must never start with (a primary/commit/destructive action).
const UNSAFE_LEADING_VERBS = new Set([
  'delete',
  'destroy',
  'remove',
  'disable',
  'deactivate',
  'approve',
  'send',
  'finalize',
  'revoke',
  'reset',
  'purge',
  'wipe',
  'confirm',
  'submit',
  'save',
  'löschen',
  'entfernen',
  'bestätigen',
  'senden',
  'speichern',
]);

/**
 * Dismiss an open modal SAFELY. Assert the dialog's identifying text FIRST (so we dismiss the dialog
 * we think we are), then press Escape — the version-agnostic safe cancel. If Escape does not close
 * it, fall back to an EXACT-match negative/cancel-label button, refusing any destructive-labelled
 * button. NEVER the primary/first button, which can be the destructive control depending on button
 * order.
 */
export async function dismissModal(
  page: Page,
  { dialog, expectedText, cancelLabel, safeLabels = [] }: DismissOptions,
): Promise<void> {
  await dialog.waitFor({ state: 'visible' });

  // Refuse to act if more than one dialog is open — we cannot prove which one our actions hit.
  const visibleDialogs = await page.getByRole('dialog').count();
  if (visibleDialogs !== 1) {
    throw new Error(
      `dismissModal: expected exactly one open dialog, found ${visibleDialogs}. Close extra ` +
        'dialogs before dismissing so the dismiss action cannot hit the wrong one.',
    );
  }

  // Identity check before we touch anything — fail loudly if this is not the dialog we expect.
  // Exact text match so a destructive button label that merely contains expectedText cannot satisfy it.
  await dialog.getByText(expectedText, { exact: true }).first().waitFor({ state: 'visible' });

  await page.keyboard.press('Escape');
  if (await dialog.waitFor({ state: 'hidden', timeout: 1000 }).then(() => true, () => false)) return;

  // Escape did not close it — use a known-safe negative control from the allowlist, never the
  // primary button.
  if (cancelLabel !== undefined) {
    const allowed = new Set([...DEFAULT_SAFE_LABELS, ...safeLabels]);
    const leadingToken = tokenize(cancelLabel)[0];
    if (!allowed.has(cancelLabel) || (leadingToken !== undefined && UNSAFE_LEADING_VERBS.has(leadingToken))) {
      throw new Error(
        `dismissModal: refusing to dismiss via "${cancelLabel}" — it is not a recognized safe ` +
          'negative label. Pass an exact safe-negative label (Cancel/No/Close/…) or extend ' +
          'safeLabels with a verified non-destructive label; never a commit/destructive button.',
      );
    }
    // Exact match so "Cancel" does not also resolve "Cancel and delete".
    await dialog.getByRole('button', { name: cancelLabel, exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    return;
  }
  throw new Error(
    'dismissModal: Escape did not close the dialog and no cancelLabel was supplied. Pass the ' +
      'verbatim negative/cancel button label — never dismiss via the primary/first button.',
  );
}

export interface MaskOptions {
  /** The opaque inner dialog/region to mask, shoot, and scan — scoped so sibling content cannot bleed in. */
  dialog: Locator;
  /** Selectors whose text nodes + form-control values must be masked. */
  selectors: string[];
  /** The obvious placeholder string written over masked content. Masked nodes are excluded from the
   *  leak scan by identity (a data-marker), so the placeholder text is never scanned. */
  placeholder: string;
  /** Patterns that signal a real-identifier LEAK if any matches after masking. */
  patterns: RegExp[];
  /** Exact number of targets the mask must cover; a mismatch is a coverage failure (throws). */
  expectedCount: number;
}

/**
 * Mask PII inside the opaque inner dialog, then prove the mask held (capture-safety.md "Mask
 * reproducibly, then prove the mask held"). The order is load-bearing:
 *   1. Mask FIRST — overwrite text nodes AND form-control values for every `selectors` target, and
 *      TAG each masked element (a data-marker on the element) so the scan can EXCLUDE it. For a
 *      <select>, replace the rendered selected-option TEXT (and its value), not just `.value`
 *      (setting `.value` alone leaves the visible option label intact → PII ships). Count matches
 *      for the coverage assert.
 *   2. Coverage assert — matched count must equal expectedCount, so a missed target (renamed column,
 *      drifted selector) throws instead of leaking unmatchable PII.
 *   3. Scan the UNMASKED remainder — walk the whole subtree but skip any node inside a tagged
 *      (masked) element, and skip tagged form controls. We do NOT string-strip the placeholder
 *      (that fuses an adjacent unmasked value into a false negative, e.g. placeholder "redacted" +
 *      "redacted@x.com" → "@x.com" which no longer matches an e-mail regex). Excluding masked nodes
 *      by identity catches PII the author forgot to list while never re-flagging a correctly-masked
 *      target. Join the per-node strings with '\n' so adjacent cells cannot fuse into a false token.
 *
 * Both the masking and the scan recurse into OPEN shadow roots, so PII rendered by a web component
 * is masked and scanned too. CLOSED shadow roots are inaccessible to script and are NOT reached — a
 * project that renders PII inside a closed shadow root must mask it another way (e.g. inside the
 * component, or by switching the component to an open root for capture).
 *
 * SCAN CARVE-OUT (what the automated leak-scan does NOT cover): the scan reads rendered DOM TEXT
 * NODES + form-control values + input/textarea placeholder. It does NOT read CSS pseudo-element
 * content (::before/::after `content`), which IS painted into the screenshot; nor the `alt` text a
 * broken/failed <img> paints into the frame (browser replacement-rendering — painted, but not a DOM
 * text node, so the same carve-out as pseudo-content; a loaded image paints no alt); nor genuinely
 * non-rendered attributes (title/aria-label, never painted). Those surfaces rely on the human
 * eyeball-the-frame step (capture-safety.md) as the backstop — the scan is defense-in-depth, not a
 * complete proof.
 */
export async function maskAndAssert(
  page: Page,
  { dialog, selectors, placeholder, patterns, expectedCount }: MaskOptions,
): Promise<void> {
  const dialogHandle = await dialog.elementHandle();
  if (dialogHandle === null) {
    throw new Error('maskAndAssert: dialog element handle not found — cannot scope mask + assert.');
  }

  // Step 1 + 3 run in one browser evaluate: mask + tag the targets, then collect scan parts from the
  // UNMASKED remainder. Returns the matched count for the coverage assert.
  const { matched, scanParts } = await dialogHandle.evaluate(
    (root: Element, args: { selectors: string[]; placeholder: string }) => {
      const ph = args.placeholder;
      const MASKED_ATTR = 'data-handbook-masked';

      // Invoke `visit` once for every OPEN shadow root nested directly under an element in `start`'s
      // subtree (the visitor recurses for deeper roots). Closed shadow roots are inaccessible to
      // script and are skipped. Shared by queryDeep (masking) and collect (scanning) so both pierce
      // open shadow roots identically.
      const eachOpenShadowRoot = (
        start: Element | ShadowRoot,
        visit: (shadowRoot: ShadowRoot) => void,
      ): void => {
        // querySelectorAll('*') returns DESCENDANTS only, so visit `start`'s OWN open shadow root
        // first — otherwise a dialog that is itself a shadow host has its whole shadow tree skipped by
        // both the masking and scanning passes, and PII inside it would leak.
        if (start instanceof Element && start.shadowRoot) visit(start.shadowRoot);
        for (const el of Array.from(start.querySelectorAll('*'))) {
          const sr = (el as Element & { shadowRoot: ShadowRoot | null }).shadowRoot;
          if (sr) visit(sr);
        }
      };

      // querySelectorAll that pierces OPEN shadow roots: collect matches in the light DOM and in
      // every open shadowRoot reachable under `start`.
      const queryDeep = (start: Element | ShadowRoot, selector: string): Element[] => {
        const out: Element[] = [];
        for (const el of Array.from(start.querySelectorAll(selector))) out.push(el);
        eachOpenShadowRoot(start, (sr) => out.push(...queryDeep(sr, selector)));
        return out;
      };

      const maskFormOrText = (el: Element): void => {
        if (el instanceof HTMLSelectElement) {
          // A <select>: mask the label AND value of EVERY option (el.options), not only the selected
          // ones. A <select multiple>/[size>1] renders its UNSELECTED options too, so an unselected
          // option label containing PII would otherwise be visible in the shot while the tagged
          // control is excluded from the scan — a real leak. Masking all options also covers the
          // closed single-select dropdown (its hidden options are masked harmlessly).
          for (const opt of Array.from(el.options)) {
            opt.text = ph;
            opt.value = ph;
          }
          el.value = ph;
          // A <select> can carry a placeholder-style attribute too; overwrite it so a masked but
          // empty control never keeps showing PII placeholder text in the shot.
          if (el.getAttribute('placeholder')) el.setAttribute('placeholder', ph);
        } else if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
          el.value = ph;
          // Overwrite a present placeholder so a MASKED empty input does not keep painting PII
          // (the value alone is blank; the placeholder is what renders).
          if (el.getAttribute('placeholder')) el.setAttribute('placeholder', ph);
        } else {
          el.textContent = ph;
        }
      };

      // --- Step 1: mask + tag every selector target (light DOM + open shadow roots). ---
      let matchedCount = 0;
      for (const sel of args.selectors) {
        for (const el of queryDeep(root, sel)) {
          matchedCount += 1;
          el.setAttribute(MASKED_ATTR, '1');
          maskFormOrText(el);
        }
      }

      const isInsideMasked = (node: Node): boolean => {
        let cur: Node | null = node;
        while (cur) {
          if (cur instanceof Element && cur.hasAttribute(MASKED_ATTR)) return true;
          // Cross a shadow boundary: a node inside a shadow root has the shadow host as the parent
          // of its root, reached via .host.
          if (cur instanceof ShadowRoot) {
            cur = cur.host;
          } else {
            if (cur === root) break;
            cur = cur.parentNode;
          }
        }
        return false;
      };

      // --- Step 3: collect scan parts from the UNMASKED remainder only, piercing open shadow roots. ---
      const parts: string[] = [];

      const collect = (subtreeRoot: Element | ShadowRoot): void => {
        // Text nodes not inside a masked element.
        const doc = (subtreeRoot as Element).ownerDocument || document;
        const walker = doc.createTreeWalker(subtreeRoot, NodeFilter.SHOW_TEXT);
        let node = walker.nextNode();
        while (node) {
          const t = node.nodeValue || '';
          if (t.trim().length > 0 && !isInsideMasked(node)) parts.push(t);
          node = walker.nextNode();
        }
        // Untagged form-control values + placeholder + option labels (the text walk misses input
        // values, placeholders, and option text). Skip any control we tagged as masked.
        for (const el of Array.from(subtreeRoot.querySelectorAll('input, textarea, select'))) {
          if (el.hasAttribute(MASKED_ATTR)) continue;
          if (el instanceof HTMLSelectElement) {
            // Scan EVERY option's label and value (el.options), not only the selected ones: a
            // <select multiple>/[size>1] visibly renders unselected options, so their labels must be
            // leak-scanned too when the control is not masked.
            for (const opt of Array.from(el.options)) {
              parts.push(opt.text);
              parts.push(opt.value);
            }
          } else if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
            parts.push(el.value);
            // An unmasked input with an empty value but a PII placeholder (e.g.
            // placeholder="jane@example.com") still paints that text into the shot, so scan it.
            const ph2 = el.getAttribute('placeholder');
            if (ph2) parts.push(ph2);
          }
        }
        // Recurse into every open shadow root in this subtree.
        eachOpenShadowRoot(subtreeRoot, (sr) => collect(sr));
      };
      collect(root);

      return { matched: matchedCount, scanParts: parts };
    },
    { selectors, placeholder },
  );

  // Step 2: coverage assert — a missed target throws instead of leaking.
  if (matched !== expectedCount) {
    throw new Error(
      `maskAndAssert: masked ${matched} target(s) but expected ${expectedCount} — the mask missed ` +
        'a target (selector drift / renamed field). Fail-closed: unmatchable PII would otherwise leak.',
    );
  }

  // Leak assert over the UNMASKED remainder — no placeholder stripping needed (masked nodes are
  // excluded by identity, not by string-substituting the placeholder out of the corpus).
  const scan = scanParts.join('\n');
  for (const pattern of patterns) {
    // Rebuild without g/y so .test is stateless across patterns/calls.
    const safe = new RegExp(pattern.source, pattern.flags.replace(/[gy]/g, ''));
    if (safe.test(scan)) {
      throw new Error(`maskAndAssert: a real identifier survived masking (matched ${pattern}).`);
    }
  }
}

// Re-export so a spec can keep one import site if it wants the Browser type for context creation.
export type { Browser };
