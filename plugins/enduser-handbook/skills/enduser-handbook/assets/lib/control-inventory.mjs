// enduser-handbook capture asset — non-normative reference implementation for the Playwright
// reference case. The normative, engine-agnostic contract lives in
// references/capture-spec-helpers.md (and capture-safety.md / page-identity.md). Fork for other
// engines.
//
// control-inventory.mjs — the pure, browser-agnostic logic behind the surface-enumeration pass
// (completeness-gate.md: "a short script that enumerates triggers …"): per-control extraction +
// normalization AND the scoped-selector composition (buildScopedSelector).
// This file is plain ESM with NO browser dependency: extractRecord() takes an element-handle-like
// object (anything exposing async getAttribute(name) + async evaluate(fn)), so it runs Node-side
// and is unit-testable without Playwright. The Playwright reference impl
// (surface-audit.playwright.ts) feeds it real ElementHandles; tests/control-inventory.test.mjs
// feeds it stub handles. Keeping extraction here — rather than inside a $$eval browser callback —
// is what makes the "icon-only control dropped" regression catchable by a unit test.

// DOM attribute names this layer reads, kept as a single source of truth. These are the REAL
// lowercase/hyphenated DOM names (handle.getAttribute is case-sensitive on hyphenated names), not
// the camelCase keys of the returned record.
const ATTR_TITLE = 'title';
const ATTR_ARIA_LABEL = 'aria-label';
const ATTR_HREF = 'href';
const ATTR_ROLE = 'role';
const ATTR_TESTID = 'data-testid';
const ATTR_TYPE = 'type';
const ATTR_VALUE = 'value';
const ATTR_NAME = 'name';
const ATTR_CLASS = 'class';
// A native <input type="submit|button|reset|image"> carries its visible LABEL in the `value`
// attribute, not textContent — reading it keeps "<input type=submit value=Delete>" from becoming a
// label-less, mis-counted control. BUT a text-entry input's `value` attribute can be PREFILLED USER
// DATA (a server-rendered `<input type=text value="jane@example.com">`), and extractRecord's output
// is console.logged by the surface audit — so `value` is read ONLY for these button-like input types
// and is nulled for every other control, to avoid leaking PII into the inventory.
const BUTTON_LIKE_INPUT_TYPES = new Set(['submit', 'button', 'reset', 'image']);

// Broad interactive-surface selector — what the surface audit enumerates as a control.
// surface-audit.playwright.ts imports this for enumeration (via buildScopedSelector + page.$$). It lives
// in this pure module (the audit's logic lib, beside buildScopedSelector) so it is unit-testable.
//
// Covers buttons, EVERY non-hidden native input (input:not([type=hidden]) — submit/button/reset/image,
// checkbox/radio, text/email/etc., AND date/time/range/color/file), textareas, [contenteditable]
// regions, selects, disclosure controls (summary), real links, ARIA-role controls, menu items,
// icon-only controls (an aria-labelled element with no text), status badges, download anchors, and
// mailto links. A fixed input-type allowlist silently omits whichever type the author forgot, so we
// match all non-hidden inputs by tag. Deliberately NOT text-gated — a control with empty text but an
// aria-label is in. Treat this list as a heuristic starting point, not a hard gate (greps are
// evadeable); the human classify pass in completeness-gate.md is authoritative.
export const INTERACTIVE_SELECTOR = [
  'button',
  'input:not([type=hidden])',
  'textarea',
  '[contenteditable]',
  'select',
  'summary',
  'a[href]',
  '[role=button]',
  '[role=menuitem]',
  '[role=tab]',
  '[role=switch]',
  '[role=link]',
  '[aria-label]',
  '[data-testid]',
  '.badge',
  '[role=status]',
  'a[download]',
  'a[href^="mailto:"]',
  // Framework button/toggle classes (Bootstrap et al.): a glyph/icon control styled as a button
  // ('<span class="btn glyphicon-trash">', '<div data-bs-toggle="dropdown">') is invisible to a
  // tag/role/href-only enumeration. These are ENUMERATED as controls so they are counted, but they
  // are NOT "genuine controls" (isGenuineControl keys off tag/role/href/editable, never class) — a
  // non-genuine '<span class="btn">' therefore keeps its text SUPPRESSED, preserving the PII-leak
  // whitelist (a '<span class="btn">Jane Doe</span>' must not leak its label).
  '.btn',
  '[data-bs-toggle]',
  '[data-toggle]',
].join(', ');

// Value-bearing controls hold USER DATA, not a static label, in textContent (or value): a <textarea>
// (prefilled notes), a <select> (its textContent is the concatenated option list — possibly customer
// names/emails), a non-button <input> (the value-leak class above), and any element that is EFFECTIVELY
// contenteditable (inline-edit content). This release widened the audit selector to include exactly
// these, so their textContent would otherwise flow into the console-logged inventory + the committed
// coverage matrix. For these controls we SUPPRESS text and identify them by non-sensitive metadata
// (name / aria-label / title / testid) instead. <button>/<a>/menu items are NOT value-bearing — their
// textContent is the visible label and must be kept (and classified). isValueBearing decides per record.
//
// `editable` is the EFFECTIVE editability (el.isContentEditable), not the own contenteditable
// attribute: editability is INHERITED, so an aria-labelled/testid child inside a contenteditable host
// is editable while getAttribute('contenteditable') on the child is null. Reading the resolved boolean
// in the browser (and respecting contenteditable="false") is what closes that inherited-leak path.
function isValueBearing(tag, type, editable) {
  if (editable) return true;
  if (tag === 'TEXTAREA' || tag === 'SELECT') return true;
  if (tag === 'INPUT' && !BUTTON_LIKE_INPUT_TYPES.has(type.toLowerCase())) return true;
  return false;
}

// A genuinely-interactive LEAF control — one whose visible text IS its actionable label (a button's
// "Save", a link's "Download"). extractRecord logs raw text ONLY for these. Anything matched merely via
// a broad identity attr ([aria-label]/[data-testid]/.badge/[role=status]) that is NOT itself a control
// — a <div data-testid> data region or row — has its textContent SUPPRESSED, because that text is
// aggregate page data (row PII), not a control label. (A genuine control whose label ITSELF contains
// PII — a clickable customer name — is the accepted identity-field boundary in completeness-gate.md,
// handled by seeded data + the human scrub, not by dropping the label the matrix needs.) Determined
// from tag/role/href, never from the identity attrs, since those are exactly the ambiguous matchers.
const GENUINE_CONTROL_TAGS = new Set(['BUTTON', 'INPUT', 'SELECT', 'TEXTAREA', 'SUMMARY']);
const GENUINE_CONTROL_ROLES = new Set(['button', 'menuitem', 'tab', 'switch', 'link']);

function isGenuineControl({ tag, role, href, editable }) {
  if (editable) return true;
  if (tag === 'A') return href != null; // a link is a control only when it actually navigates (has href)
  if (GENUINE_CONTROL_TAGS.has(tag)) return true;
  return role != null && GENUINE_CONTROL_ROLES.has(role.toLowerCase());
}

// CSS form of the SAME "genuine control" notion, used to detect a genuine control DESCENDANT: does this
// element WRAP a real control → it is a CONTAINER (a clickable row that also holds a <button>, a
// role=button card around child controls), so its textContent aggregates the wrapped controls' + data
// text (row PII) and must be suppressed EVEN IF the element is itself genuine. Deliberately EXCLUDES the
// identity-only matchers ([aria-label]/[data-testid]/.badge/[role=status]) so a non-control instrumented
// label span (e.g. <button><span data-testid=x>Save</span></button>) does NOT count as a descendant
// control and the button keeps its label. Keep in sync with isGenuineControl above.
const GENUINE_CONTROL_SELECTOR = [
  'button',
  'input',
  'select',
  'textarea',
  'summary',
  'a[href]',
  '[contenteditable]',
  '[role=button]',
  '[role=menuitem]',
  '[role=tab]',
  '[role=switch]',
  '[role=link]',
].join(', ');

// True when a string contains a destructive verb token (EN/DE) under the same tokenizer
// classifyByShape uses. Extracted so a <select>'s option list can be checked for a destructive ACTION
// token WITHOUT the raw (possibly PII) option text being stored — see extractRecord/hasDestructiveText.
function containsDestructiveToken(value) {
  if (!value) return false;
  for (const token of tokenize(value)) {
    if (DESTRUCTIVE_VERBS.has(token)) return true;
  }
  return false;
}

// Destructive verbs (EN + DE) matched against NORMALIZED TOKENS, not a raw \b word boundary. A bare
// regex misses camelCase/snake_case joins ("deleteUser", "remove_item", "destroyAccount") because
// \b treats "_" as a word char and camelCase has no boundary char at all. We tokenize the label /
// href / testId on camel/snake/kebab/URL boundaries first, then match a verb on the resulting
// tokens. This is a HINT only — see classifyByShape.
const DESTRUCTIVE_VERBS = new Set(['delete', 'remove', 'destroy', 'trash', 'löschen', 'entfernen']);

// Split a string into normalized lowercase tokens across camelCase, snake_case, kebab-case, and
// URL path/query separators. "deleteUser" → ['delete','user']; "/items/5/delete" →
// ['items','5','delete']; "remove_item" → ['remove','item'].
function tokenize(value) {
  if (!value) return [];
  return String(value)
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .split(/[^A-Za-z0-9äöüßÄÖÜ]+/)
    .map((t) => t.toLowerCase())
    .filter((t) => t.length > 0);
}

/**
 * Read one control's identity from an element-handle-like object.
 *
 * Uses ONLY two capabilities so any engine handle (or a test stub) can satisfy it:
 *   - await handle.getAttribute(name)  → DOM attribute or null
 *   - await handle.evaluate(fn, arg)   → fn runs against the element; we read tagName + textContent +
 *     isContentEditable, and probe el.querySelector(arg) for a genuine-control descendant
 *
 * MUST ALWAYS return a record. It NEVER returns null/undefined and NEVER filters. An icon-only
 * control (empty text but an aria-label) MUST survive — dropping it here is the exact bug this
 * plugin exists to prevent (a short control count → a fabricated coverage matrix). Missing
 * attributes become null; a missing attribute is data, not a reason to discard the control.
 *
 * @param {{ getAttribute: (name: string) => Promise<string|null>,
 *           evaluate: (fn: (el: { tagName: string, textContent: string, isContentEditable: boolean,
 *                                 querySelector: (selector: string) => any }, arg: string) => any,
 *                      arg?: string) => Promise<any> }} handle
 * @returns {Promise<{ tag: string|null, text: string, value: string|null, hasDestructiveText: boolean,
 *                      name: string|null, title: string|null, ariaLabel: string|null,
 *                      href: string|null, role: string|null, testId: string|null,
 *                      className: string|null }>}
 */
export async function extractRecord(handle) {
  // tagName + textContent come from the element itself, not from an attribute. We read them in one
  // evaluate round-trip and never throw if textContent is absent.
  const shape = await handle.evaluate(
    (el, genuineControlSel) => ({
      tagName: el.tagName,
      textContent: el.textContent,
      // Effective (inherited) editability — true for a child inside a contenteditable host, which
      // getAttribute('contenteditable') misses; false for contenteditable="false". See isValueBearing.
      isContentEditable: el.isContentEditable === true,
      // Does this element WRAP a genuine control? If so it is a CONTAINER (a clickable row that also
      // holds a <button>, a role=button card around child controls), and its textContent aggregates
      // descendant data (row PII), not a clean label — so it must NOT be logged even when the element
      // itself is genuine. Uses the NARROW genuine-control selector (NOT the identity-only attrs), so a
      // non-control instrumented label span does not count and a plain leaf button keeps its label.
      hasGenuineControlDescendant:
        typeof el.querySelector === 'function' && el.querySelector(genuineControlSel) !== null,
    }),
    GENUINE_CONTROL_SELECTOR,
  );

  const tag = shape?.tagName ?? null;
  const rawText = (shape?.textContent ?? '').trim();
  const editable = shape?.isContentEditable === true;
  const hasGenuineControlDescendant = shape?.hasGenuineControlDescendant === true;

  const type = (await handle.getAttribute(ATTR_TYPE)) ?? '';
  const role = await handle.getAttribute(ATTR_ROLE);
  const href = await handle.getAttribute(ATTR_HREF);
  // `class` is developer-authored (utility/framework/icon classes) — read VERBATIM and never
  // suppressed. It is primarily the destructive heuristic's signal (glyphicon-trash / fa-trash /
  // mdi-delete) for an icon-only control that carries no other label, and the only identity such a
  // control has beyond aria-label. A minority of apps encode record/user slugs into class names, so
  // className is a verbatim console/matrix field under the SAME documented PII boundary as the
  // identity labels (seeded data + human scrub) — see completeness-gate.md; it is NOT redacted.
  const className = await handle.getAttribute(ATTR_CLASS);
  const valueBearing = isValueBearing(tag, type, editable);

  // Log raw textContent ONLY for a genuine LEAF control — genuine (its text could be its actionable
  // label, a button's "Save") AND a leaf (no genuine control nested inside, so the text is JUST its own
  // label, not aggregate row data). Suppress in every other case, since the inventory is console.logged
  // + committed:
  //   - value-bearing controls (textarea/select/non-button input/contenteditable) → user-entered data;
  //   - non-genuine elements matched only via a broad identity attr ([aria-label]/[data-testid]/.badge/
  //     [role=status]) — a <div data-testid> data region or row — whose text is aggregate page data;
  //   - genuine CONTAINERS (a role=button / a[href] row that wraps a child control) — their textContent
  //     aggregates the wrapped controls' + data text (row PII), not a clean label.
  // The real controls inside any container are enumerated as their OWN records, so nothing actionable is
  // lost. "" (not null) keeps the empty-text sentinel icon-only controls use. (A genuine LEAF control
  // whose own label is itself PII is the accepted identity-field boundary — see completeness-gate.md.)
  const genuineLeaf =
    isGenuineControl({ tag, role, href, editable }) && !hasGenuineControlDescendant;
  const suppressText = valueBearing || !genuineLeaf;
  const text = suppressText ? '' : rawText;

  // A <select>'s suppressed option list can still carry a destructive ACTION token (a change-driven
  // "Delete selected" option). Keep ONLY a boolean hint derived from it — never the raw labels — so
  // classifyByShape still flags the select without its (possibly PII) option text entering the
  // inventory. SELECT-only: in a textarea/contenteditable/input "delete" is user content, not an
  // action, and must NOT flag.
  const hasDestructiveText = tag === 'SELECT' && containsDestructiveToken(rawText);

  // Type-aware value: keep it only for button-like inputs (where value IS the label); null it for
  // text-entry inputs (and everything else) so a prefilled field's user data never enters the
  // console-logged inventory.
  const value =
    tag === 'INPUT' && BUTTON_LIKE_INPUT_TYPES.has(type.toLowerCase())
      ? await handle.getAttribute(ATTR_VALUE)
      : null;

  return {
    tag,
    text,
    value,
    // A non-PII boolean: a <select> whose option list contains a destructive verb (the raw options are
    // never stored). Lets classifyByShape keep flagging change-driven destructive selects.
    hasDestructiveText,
    // `name` is the developer-set form-field name ("notes", "user[email]"), NOT the user's data — safe
    // to log, and the only stable identity an unlabelled <textarea name=notes>/<select name=status> has
    // once its textContent is suppressed. Without it those rows collapse to "(unlabelled control)".
    name: await handle.getAttribute(ATTR_NAME),
    title: await handle.getAttribute(ATTR_TITLE),
    ariaLabel: await handle.getAttribute(ATTR_ARIA_LABEL),
    href,
    role,
    testId: await handle.getAttribute(ATTR_TESTID),
    className,
  };
}

/**
 * Heuristic side-effect hint for a single record. Returns 'candidate-destructive' when a
 * destructive verb (EN/DE) or a delete-shaped route appears in the label, href, or test id;
 * otherwise 'unclassified'.
 *
 * This is a HINT, never a decision. The authoritative side-effect classification is the human
 * classify pass in references/completeness-gate.md (read against the running UI and the project's
 * capture.live_action_examples). A 'candidate-destructive' flag means "look here first", and an
 * 'unclassified' result means "the heuristic saw nothing" — it does NOT mean "safe".
 *
 * @param {{ text: string, value?: string|null, hasDestructiveText?: boolean, title: string|null,
 *           ariaLabel: string|null, href: string|null, role: string|null, testId: string|null,
 *           className?: string|null }} record
 * @returns {'candidate-destructive' | 'unclassified'}
 */
export function classifyByShape(record) {
  // A <select> whose suppressed option list held a destructive verb carries that signal as a boolean
  // (hasDestructiveText); its raw options were never stored, so honor the precomputed hint first.
  if (record.hasDestructiveText) return 'candidate-destructive';
  // Scan the human-meaningful fields. text + value are included so a literal "Delete" button label —
  // whether it lives in textContent or in a native input's `value` attribute — is flagged even when
  // it carries no href/testId. className is included so an icon class (glyphicon-trash / fa-trash /
  // bi-trash / mdi-delete) on a label-less control still flags. Each value is tokenized so
  // camelCase/snake_case/kebab/URL-segment joins (deleteUser, remove_item, /items/5/delete,
  // glyphicon-trash) match the bare verb; matching is token-exact so "removed-item"/"undelete" do not.
  const fields = [record.text, record.value, record.title, record.ariaLabel, record.href, record.testId, record.className];
  for (const value of fields) {
    if (containsDestructiveToken(value)) return 'candidate-destructive';
  }
  return 'unclassified';
}

/**
 * Normalize a list of raw records: attach a shape hint and a positional index to each. Drops
 * NOTHING.
 *
 * There is deliberately NO content-based de-duplication. A key built from extracted attributes
 * collapses genuinely distinct controls — two empty-text icon buttons with aria-labels 'Edit' and
 * 'Delete', or two identical-looking rows of a list — into one, which is the exact "dropped
 * control" failure this plugin exists to prevent. The caller (surface-audit) enumerates each DOM
 * element exactly once via a single `page.$$(selector)` pass, so each record already corresponds to
 * one real control; there is nothing to de-duplicate. MUST NOT filter by text presence either —
 * icon-only controls survive.
 *
 * @param {Array<object>} raw
 * @returns {Array<object>} every record, each with an added `index` and `shape` field
 */
export function normalizeControls(raw) {
  return raw.map((record, index) => ({ ...record, index, shape: classifyByShape(record) }));
}

/**
 * Compose the enumeration selector for surface-audit. With no rowSelector, return the interactive
 * selector unchanged (whole-page enumeration). With a rowSelector, match interactive DESCENDANTS of
 * the scope AND the scope element ITSELF when it is interactive (a clickable row that is itself a
 * button/link/[role=…] would be missed by a descendant-only `scope :is(...)`). The two branches are
 * disjoint (an element is never its own descendant), so each control is still counted exactly once.
 *
 * The scope is wrapped as its OWN :is() group — `:is(${rowSelector})` — BEFORE composition. Without
 * that wrap a comma-separated scope like ".customer-row, .order-row" splits the surrounding :is()
 * list, leaving a bare ".customer-row" as a standalone member that matches a NON-interactive row
 * container; extractRecord would then copy that container's full textContent (row PII) into the
 * inventory. Wrapping keeps the comma INSIDE the group so only descendant/self interactive controls
 * match. This pure composition is unit-tested (tests/control-inventory.test.mjs) because a grep on
 * the .ts cannot prove the comma-list grouping is correct.
 *
 * @param {string|null|undefined} rowSelector
 * @param {string} interactiveSelector
 * @returns {string}
 */
export function buildScopedSelector(rowSelector, interactiveSelector) {
  if (!rowSelector) return interactiveSelector;
  const scope = `:is(${rowSelector})`;
  return `:is(${scope} :is(${interactiveSelector}), ${scope}:is(${interactiveSelector}))`;
}
