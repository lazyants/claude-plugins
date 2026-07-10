// Unit tests for the surface-enumeration extraction/normalization layer. Zero deps — runs under
// Node's built-in test runner: `node --test control-inventory.test.mjs`.
//
// This is the REAL regression catcher for the headline bug: an icon-only destructive control
// (empty text, only an aria-label / a /delete href) MUST survive extraction AND normalization. The
// shipped bug was a `text || href` filter that silently dropped exactly these controls, producing a
// short control count → a fabricated coverage matrix.

import test from 'node:test';
import assert from 'node:assert/strict';

import {
  extractRecord,
  normalizeControls,
  classifyByShape,
  buildScopedSelector,
  matrixLabel,
  INTERACTIVE_SELECTOR,
} from '../skills/enduser-handbook/assets/lib/control-inventory.mjs';

// Promise-accurate, DOM-name-accurate stub element handle. getAttribute is keyed on REAL DOM
// attribute names ('aria-label', 'data-testid', 'href', 'role', 'title') — not camelCase — and
// returns null for any attribute the control does not carry, mirroring Playwright's ElementHandle.
// evaluate runs the callback against a fake element exposing uppercase tagName + textContent.
function stub(attrs, { tag = 'BUTTON', text = '', editable = false, hasGenuineChild = false } = {}) {
  return {
    async getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(attrs, name) ? attrs[name] : null;
    },
    async evaluate(fn, arg) {
      // isContentEditable models the browser's EFFECTIVE (inherited) editability — a resolved boolean,
      // not the own contenteditable attribute. querySelector models whether the element wraps a GENUINE
      // control descendant (a container vs a leaf); it ignores its selector arg and answers from the flag.
      const el = {
        tagName: tag,
        textContent: text,
        isContentEditable: editable,
        querySelector: () => (hasGenuineChild ? {} : null),
      };
      return fn(el, arg);
    },
  };
}

test('extractRecord keeps an icon-only control (empty text, has aria-label)', async () => {
  const record = await extractRecord(stub({ 'aria-label': 'Filter' }, { tag: 'BUTTON', text: '' }));
  assert.ok(record, 'must return a record, not null/undefined');
  assert.equal(record.text, '');
  assert.equal(record.ariaLabel, 'Filter');
  assert.equal(record.tag, 'BUTTON');
});

test('extractRecord keeps a destructive icon delete link (empty text, /delete href, DE aria-label)', async () => {
  const record = await extractRecord(
    stub({ href: '/items/5/delete', 'aria-label': 'Löschen' }, { tag: 'A', text: '' }),
  );
  assert.ok(record, 'must return a record, not null/undefined');
  assert.equal(record.text, '');
  assert.equal(record.href, '/items/5/delete');
  assert.equal(record.ariaLabel, 'Löschen');
  assert.equal(record.tag, 'A');
});

test('extractRecord returns nulls for missing attrs, does not throw, preserves uppercase tagName', async () => {
  const record = await extractRecord(stub({}, { tag: 'BUTTON', text: 'Save' }));
  assert.equal(record.text, 'Save');
  assert.equal(record.tag, 'BUTTON');
  assert.equal(record.value, null);
  assert.equal(record.hasDestructiveText, false);
  assert.equal(record.name, null);
  assert.equal(record.title, null);
  assert.equal(record.ariaLabel, null);
  assert.equal(record.href, null);
  assert.equal(record.role, null);
  assert.equal(record.testId, null);
});

test('extractRecord reads the value of a BUTTON-LIKE input; a native submit with a destructive value is flagged', async () => {
  // <input type="submit" value="Delete"> has empty textContent and no aria-label/href — the label
  // lives ONLY in the value attribute. Missing it would drop a destructive control from the matrix
  // (the same short-control-count failure as the icon-only case).
  const record = await extractRecord(stub({ type: 'submit', value: 'Delete' }, { tag: 'INPUT', text: '' }));
  assert.equal(record.text, '');
  assert.equal(record.value, 'Delete');
  assert.equal(record.tag, 'INPUT');
  assert.equal(classifyByShape(record), 'candidate-destructive');
  // It also survives normalization (nothing is dropped for empty text).
  assert.equal(normalizeControls([record]).length, 1);
});

test('extractRecord does NOT copy a text-entry input value (prefilled PII must not enter the inventory)', async () => {
  // A server-rendered <input type="text" value="jane.smith@example.com"> carries USER DATA in value.
  // The inventory is console.logged, so value must be nulled for text-entry inputs — only
  // VALUE-LABELLED inputs (submit/button/reset) expose value (where it is the visible label); `image`
  // is deliberately excluded — see the matrixLabel/image-input tests below.
  for (const type of ['text', 'email', 'password', 'search', 'tel', 'url', 'number']) {
    const record = await extractRecord(stub({ type, value: 'jane.smith@example.com' }, { tag: 'INPUT', text: '' }));
    assert.equal(record.value, null, `value must be null for input[type=${type}] (PII)`);
  }
  // A typeless input defaults to text → value is PII → nulled.
  const typeless = await extractRecord(stub({ value: 'jane.smith@example.com' }, { tag: 'INPUT', text: '' }));
  assert.equal(typeless.value, null);
});

test('extractRecord does NOT copy textContent of value-bearing controls (textarea/select/contenteditable PII)', async () => {
  // This release widened the audit selector to include textarea/select/[contenteditable]. Their
  // textContent is USER DATA — a prefilled note, the concatenated option list (customer names/emails),
  // inline-edit content — and extractRecord's output is console.logged + committed in the coverage
  // matrix. So text MUST be suppressed for these, exactly like a text-entry input's value. The control
  // is still kept (never dropped); its identity comes from name/aria-label, not the leaked content.
  const PII = 'jane.smith@example.com';

  // <textarea name="notes">jane.smith@example.com …</textarea> — prefilled note.
  const textarea = await extractRecord(
    stub({ name: 'notes' }, { tag: 'TEXTAREA', text: `${PII} — call back Tuesday` }),
  );
  assert.equal(textarea.text, '', 'textarea textContent (a prefilled note) must not be copied');
  assert.equal(textarea.name, 'notes', 'name is the non-PII identity that replaces the suppressed text');
  assert.ok(!JSON.stringify(textarea).includes(PII), 'no PII anywhere in the textarea record');

  // <select name="assignee"> whose option list concatenates into textContent with customer emails.
  // No destructive option → text suppressed, no PII, and hasDestructiveText stays false.
  const select = await extractRecord(
    stub({ name: 'assignee' }, { tag: 'SELECT', text: `Unassigned ${PII} bob@example.com` }),
  );
  assert.equal(select.text, '', 'select option-list textContent must not be copied');
  assert.equal(select.hasDestructiveText, false);
  assert.ok(!JSON.stringify(select).includes(PII), 'no PII anywhere in the select record');

  // An EFFECTIVELY editable host (el.isContentEditable === true) — a <div contenteditable> inline
  // editor. Its content is user data → suppressed; identity comes from aria-label.
  const editableHost = await extractRecord(
    stub({ 'aria-label': 'Description' }, { tag: 'DIV', text: PII, editable: true }),
  );
  assert.equal(editableHost.text, '', 'contenteditable host content must not be copied');
  assert.equal(editableHost.ariaLabel, 'Description');
  assert.ok(!JSON.stringify(editableHost).includes(PII), 'no PII in the contenteditable host record');

  // Inherited-editability regression (finding 2): a SPAN whose OWN contenteditable attribute is null
  // but which is effectively editable because an ancestor host is. It matched the selector via
  // data-testid. getAttribute('contenteditable') would miss it; el.isContentEditable does not.
  const inheritedChild = await extractRecord(
    stub({ 'data-testid': 'inline-cell' }, { tag: 'SPAN', text: PII, editable: true }),
  );
  assert.equal(inheritedChild.text, '', 'inherited-editable child content must not be copied');
  assert.ok(!JSON.stringify(inheritedChild).includes(PII), 'no PII in the inherited-editable child record');

  // The records are NOT dropped — they survive normalization with their identity intact (the headline
  // "never drop a control" invariant still holds for value-bearing controls).
  assert.equal(normalizeControls([textarea, select, editableHost, inheritedChild]).length, 4);
});

test('contenteditable="false" is NOT treated as editable — a genuine control keeps its label', async () => {
  // el.isContentEditable === false must NOT trigger value-bearing suppression. A [role=button] is a
  // genuine control, so with editable=false its label is preserved (proves the editable=false path).
  const record = await extractRecord(
    stub({ contenteditable: 'false', role: 'button' }, { tag: 'DIV', text: 'Toggle', editable: false }),
  );
  assert.equal(record.text, 'Toggle');
});

test('a <select> destructive-option signal survives WITHOUT emitting the option text (finding 3)', async () => {
  // A change-driven bulk-action <select> whose only destructive signal is an option label
  // ("Delete selected"), mixed with PII option labels. The raw option list must NOT be copied, but the
  // destructive ACTION signal must survive as a boolean so the control is still flagged
  // candidate-destructive — otherwise blanking the text would silently drop a destructive control.
  const PII = 'jane.smith@example.com';
  const select = await extractRecord(
    stub({ name: 'bulk-action' }, { tag: 'SELECT', text: `Choose action ${PII} Delete selected` }),
  );
  assert.equal(select.text, '', 'option labels must not be copied');
  assert.equal(select.hasDestructiveText, true, 'the destructive option signal is preserved as a boolean');
  assert.ok(!JSON.stringify(select).includes(PII), 'no PII (the raw option labels) anywhere in the record');
  assert.equal(classifyByShape(select), 'candidate-destructive');
  assert.equal(normalizeControls([select])[0].shape, 'candidate-destructive');
});

test('extractRecord suppresses an identity-only CONTAINER row\'s aggregated text (wraps a button) — finding A', async () => {
  // A non-control ROW matched via a broad identity attr ([data-testid]/[aria-label]). Its textContent is
  // the whole row (customer name + email + the button label) — PII. A DIV is not a genuine control, so
  // its text is suppressed; the button inside is enumerated as its own record.
  const PII = 'jane.smith@example.com';
  const row = await extractRecord(
    stub({ 'data-testid': 'customer-row' }, { tag: 'DIV', text: `Jane ${PII} Edit` }),
  );
  assert.equal(row.text, '', 'a non-control container must not log its aggregated row text');
  assert.equal(row.testId, 'customer-row', 'identity still comes from the non-PII data-testid');
  assert.ok(!JSON.stringify(row).includes(PII), 'no row PII anywhere in the record');
});

test('extractRecord suppresses an identity-only DATA REGION\'s text — no child control at all (round-11 residual)', async () => {
  // The harder residual: a <div data-testid="customer-details">Jane jane@…</div> with NO descendant
  // control and not value-bearing. It is matched only by [data-testid], is not a genuine control, so its
  // raw text (pure PII) must NOT be logged. (This is the case a "wraps-a-control" probe missed.)
  const PII = 'jane.smith@example.com';
  const region = await extractRecord(
    stub({ 'data-testid': 'customer-details' }, { tag: 'DIV', text: `Jane ${PII}` }),
  );
  assert.equal(region.text, '', 'an identity-only data region must not log its text');
  assert.ok(!JSON.stringify(region).includes(PII), 'no PII anywhere in the record');
});

test('extractRecord suppresses a .badge / [role=status] indicator\'s text (may carry data, not a control label)', async () => {
  const badge = await extractRecord(stub({ class: 'badge' }, { tag: 'SPAN', text: 'Assigned to Jane Smith' }));
  assert.equal(badge.text, '', 'a badge is not a genuine control — its text is informational, not a label');
  const status = await extractRecord(stub({ role: 'status' }, { tag: 'DIV', text: 'Saving jane@example.com…' }));
  assert.equal(status.text, '', 'a [role=status] region is not a genuine control');
});

test('extractRecord KEEPS the label of GENUINE LEAF controls (button / a[href] / [role=button] / summary)', async () => {
  // The text of a genuine LEAF control (genuine AND no genuine-control descendant) IS its actionable
  // label — keep it (and classify it). hasGenuineChild defaults false → these are leaves.
  assert.equal((await extractRecord(stub({}, { tag: 'BUTTON', text: 'Edit' }))).text, 'Edit');
  assert.equal((await extractRecord(stub({ href: '/r' }, { tag: 'A', text: 'Download' }))).text, 'Download');
  assert.equal((await extractRecord(stub({ role: 'button' }, { tag: 'DIV', text: 'Menu' }))).text, 'Menu');
  assert.equal((await extractRecord(stub({}, { tag: 'SUMMARY', text: 'Details' }))).text, 'Details');
  // A genuine destructive control's label survives and is classified (headline mission intact).
  const del = await extractRecord(stub({}, { tag: 'BUTTON', text: 'Delete' }));
  assert.equal(del.text, 'Delete');
  assert.equal(classifyByShape(del), 'candidate-destructive');
});

test('extractRecord KEEPS a button label when its only descendant is a NON-control instrumented span', async () => {
  // <button><span data-testid="x">Save</span></button>: the span is matched by the audit's broad
  // selector but is NOT a genuine control, so hasGenuineControlDescendant is false → the button is a
  // leaf and keeps "Save". (Codex's explicit "still keep labels for buttons with instrumented spans".)
  const btn = await extractRecord(stub({}, { tag: 'BUTTON', text: 'Save', hasGenuineChild: false }));
  assert.equal(btn.text, 'Save');
});

test('extractRecord suppresses a GENUINE-by-role/href CONTAINER that wraps a child control — finding (round 12)', async () => {
  // A clickable row that is genuine by role/href BUT wraps a real control aggregates the row text
  // (PII + the child label). Genuine-self is not enough — it must also be a LEAF. hasGenuineChild=true.
  const PII = 'jane.smith@example.com';
  const roleRow = await extractRecord(
    stub({ role: 'button', 'data-testid': 'customer-row' }, { tag: 'DIV', text: `Jane ${PII} Edit`, hasGenuineChild: true }),
  );
  assert.equal(roleRow.text, '', 'a role=button container wrapping a control must not log its aggregate row text');
  assert.ok(!JSON.stringify(roleRow).includes(PII), 'no row PII in the role=button container record');

  const linkRow = await extractRecord(
    stub({ href: '/customer/5' }, { tag: 'A', text: `Jane ${PII} Edit`, hasGenuineChild: true }),
  );
  assert.equal(linkRow.text, '', 'an a[href] container wrapping a control must not log its aggregate row text');
  assert.ok(!JSON.stringify(linkRow).includes(PII), 'no row PII in the a[href] container record');
});

test('the genuine-control descendant probe uses the NARROW selector (excludes identity-only attrs)', async () => {
  // The probe must use the genuine-control selector, NOT the broad enumeration selector: otherwise a
  // non-control instrumented label span ([data-testid]) would count as a descendant and wrongly suppress
  // a leaf button's label. Capture the selector passed into evaluate and assert its membership.
  let probeSelector;
  const capturing = {
    async getAttribute() {
      return null;
    },
    async evaluate(fn, arg) {
      probeSelector = arg;
      return fn({ tagName: 'DIV', textContent: '', isContentEditable: false, querySelector: () => null }, arg);
    },
  };
  await extractRecord(capturing);
  assert.ok(probeSelector.includes('button'), 'probe must match genuine controls (button)');
  assert.ok(probeSelector.includes('[role=button]'), 'probe must match role controls');
  assert.ok(probeSelector.includes('a[href]'), 'probe must match navigable links');
  for (const identityOnly of ['[aria-label]', '[data-testid]', '.badge', '[role=status]']) {
    assert.ok(!probeSelector.includes(identityOnly), `probe must EXCLUDE the identity-only matcher ${identityOnly}`);
  }
});

test('extractRecord suppresses text for an <a> WITHOUT href (not a navigable control)', async () => {
  // An <a data-testid> with no href is matched by the audit but is not a genuine control; its text is
  // treated as data, not a label.
  const a = await extractRecord(stub({ 'data-testid': 'x' }, { tag: 'A', text: 'jane@example.com' }));
  assert.equal(a.text, '');
});

test('INTERACTIVE_SELECTOR (enumeration selector) is exported and broad', () => {
  assert.equal(typeof INTERACTIVE_SELECTOR, 'string');
  for (const part of ['button', 'input:not([type=hidden])', '[aria-label]', '[data-testid]', '.badge', '.btn', '[data-bs-toggle]', '[data-toggle]']) {
    assert.ok(INTERACTIVE_SELECTOR.includes(part), `INTERACTIVE_SELECTOR must include ${part}`);
  }
});

test('a non-genuine <span class="btn glyphicon-trash"> is enumerated but its text is SUPPRESSED; className flags destructive', async () => {
  // The headline (a): a glyph control styled as a button. The SPAN is matched by the broadened .btn
  // matcher but is NOT a genuine control (isGenuineControl never keys off class), so its text stays
  // suppressed — preserving the PII whitelist (a '<span class="btn">Jane Doe</span>' must not leak).
  // className is captured verbatim and carries the destructive signal via its glyphicon-trash token.
  const record = await extractRecord(
    stub({ 'aria-label': 'Delete', class: 'btn glyphicon-trash' }, { tag: 'SPAN', text: '' }),
  );
  assert.equal(record.text, '', 'a non-genuine .btn span must not log its text');
  assert.equal(record.className, 'btn glyphicon-trash', 'class is captured verbatim');
  assert.equal(record.ariaLabel, 'Delete');
  assert.equal(classifyByShape(record), 'candidate-destructive', 'the glyphicon-trash class token flags destructive');
});

test('a text-labelled <span class="btn">Actions</span> is SUPPRESSED (a SPAN is not genuine) but className is kept', async () => {
  // The documented trade: a text-labelled .btn span loses its visible label in the matrix (SPAN is
  // not a genuine control), recovered from className / aria-label / the human scrub. This is the
  // deliberate preservation of the v1.0.5 whitelist, NOT a regression — do not make .btn genuine.
  const record = await extractRecord(stub({ class: 'btn' }, { tag: 'SPAN', text: 'Actions' }));
  assert.equal(record.text, '', 'a non-genuine .btn span suppresses its text');
  assert.equal(record.className, 'btn', 'class is captured verbatim even when text is suppressed');
});

test('a genuine <button class="btn btn-danger">Delete</button> KEEPS its label and is flagged destructive', async () => {
  // A real Bootstrap button is already genuine via its BUTTON tag (not via class), so it keeps its
  // label and is classified — the broadened matcher does not change genuine controls.
  const record = await extractRecord(stub({ class: 'btn btn-danger' }, { tag: 'BUTTON', text: 'Delete' }));
  assert.equal(record.text, 'Delete', 'a genuine leaf button keeps its label');
  assert.equal(record.className, 'btn btn-danger');
  assert.equal(classifyByShape(record), 'candidate-destructive');
});

test('className is null when the control has no class attribute', async () => {
  const record = await extractRecord(stub({}, { tag: 'BUTTON', text: 'Save' }));
  assert.equal(record.className, null, 'a missing class attribute is null, not undefined/empty');
});

test('a class containing "removed-item" does NOT flag destructive (token-exact: removed != remove)', async () => {
  // Token-exact matching: tokenize('removed-item') → ['removed','item']; neither is a destructive
  // verb, so an otherwise-clean control with only that class must stay unclassified (no false trip).
  const record = await extractRecord(stub({ class: 'removed-item' }, { tag: 'BUTTON', text: 'Restore' }));
  assert.equal(record.className, 'removed-item');
  assert.equal(classifyByShape(record), 'unclassified', 'removed != remove — token-exact, no false positive');
});

test('a "delete"-containing textarea is NOT flagged destructive (user content, not an action)', async () => {
  // The destructive-option hint is SELECT-only. In a textarea/contenteditable the word "delete" is
  // user-typed prose, not an action label — flagging it would be a false positive (and the text is
  // never stored, so there is nothing to classify).
  const textarea = await extractRecord(
    stub({ name: 'notes' }, { tag: 'TEXTAREA', text: 'please delete my old note' }),
  );
  assert.equal(textarea.hasDestructiveText, false);
  assert.equal(classifyByShape(textarea), 'unclassified');
});

test('normalizeControls keeps icon-only + destructive records (no text-presence filter) and flags the destructive one', async () => {
  const iconOnly = await extractRecord(stub({ 'aria-label': 'Filter' }, { tag: 'BUTTON', text: '' }));
  const destructive = await extractRecord(
    stub({ href: '/items/5/delete', 'aria-label': 'Löschen' }, { tag: 'A', text: '' }),
  );

  const normalized = normalizeControls([iconOnly, destructive]);
  assert.equal(normalized.length, 2, 'both controls survive normalization — none dropped for empty text');

  const flagged = normalized.find((c) => c.href === '/items/5/delete');
  assert.ok(flagged, 'the destructive record survived');
  assert.equal(flagged.shape, 'candidate-destructive');

  const icon = normalized.find((c) => c.ariaLabel === 'Filter');
  assert.ok(icon, 'the icon-only record survived');
});

test('normalizeControls keeps two DISTINCT empty-text icon buttons (Edit vs Löschen) — no content dedup', async () => {
  // Regression for the dedup-collapse bug: two BUTTON records, empty text, only aria-labels differ.
  // A content-based dedup key that excluded ariaLabel would collapse these into one and silently
  // drop a control — the exact "icon-only control dropped" failure this plugin exists to prevent.
  const edit = await extractRecord(stub({ 'aria-label': 'Edit' }, { tag: 'BUTTON', text: '' }));
  const del = await extractRecord(stub({ 'aria-label': 'Löschen' }, { tag: 'BUTTON', text: '' }));

  const normalized = normalizeControls([edit, del]);
  assert.equal(normalized.length, 2, 'both icon buttons survive — distinct controls are not collapsed');

  const editRec = normalized.find((c) => c.ariaLabel === 'Edit');
  const delRec = normalized.find((c) => c.ariaLabel === 'Löschen');
  assert.ok(editRec, 'the Edit button survived');
  assert.ok(delRec, 'the Löschen button survived');
  assert.equal(editRec.shape, 'unclassified');
  assert.equal(delRec.shape, 'candidate-destructive', 'the destructive icon button is flagged');
});

test('buildScopedSelector: no rowSelector returns the interactive selector unchanged', () => {
  assert.equal(buildScopedSelector(undefined, 'button, a'), 'button, a');
  assert.equal(buildScopedSelector('', 'button'), 'button');
  assert.equal(buildScopedSelector(null, 'button'), 'button');
});

test('buildScopedSelector: a single-shape scope matches descendants AND the scope element itself', () => {
  assert.equal(
    buildScopedSelector('.row', 'button'),
    ':is(:is(.row) :is(button), :is(.row):is(button))',
  );
});

test('buildScopedSelector: a COMMA-SEPARATED scope is wrapped so a bare row container cannot match (PII leak regression)', () => {
  // Regression: ".customer-row, .order-row" interpolated raw would split the outer :is() list and leave
  // a standalone ".customer-row" matching a NON-interactive container, whose textContent (row PII)
  // extractRecord would then copy into the inventory. Wrapping the scope as :is(...) keeps the comma
  // inside the group so only descendant/self interactive controls match.
  const sel = buildScopedSelector('.customer-row, .order-row', 'button, a');
  assert.equal(
    sel,
    ':is(:is(.customer-row, .order-row) :is(button, a), :is(.customer-row, .order-row):is(button, a))',
  );
  // Structural proof independent of the exact string: every occurrence of the multi-shape scope is
  // immediately preceded by ":is(" — i.e. the comma never sits at the top level of a selector list.
  const scope = '.customer-row, .order-row';
  let i = 0;
  let occurrences = 0;
  while ((i = sel.indexOf(scope, i)) !== -1) {
    assert.equal(sel.slice(i - 4, i), ':is(', 'the comma-list scope must always be wrapped in :is(');
    occurrences += 1;
    i += scope.length;
  }
  assert.equal(occurrences, 2, 'the scope appears once per branch (descendant + self), both wrapped');
});

test('classifyByShape: destructive verbs (EN/DE) and delete-shaped routes are candidate-destructive', () => {
  assert.equal(classifyByShape({ text: 'Delete', title: null, ariaLabel: null, href: null, role: null, testId: null }), 'candidate-destructive');
  assert.equal(classifyByShape({ text: '', title: null, ariaLabel: 'Entfernen', href: null, role: null, testId: null }), 'candidate-destructive');
  assert.equal(classifyByShape({ text: '', title: null, ariaLabel: null, href: '/items/5/delete', role: null, testId: null }), 'candidate-destructive');
  assert.equal(classifyByShape({ text: 'Save', title: null, ariaLabel: null, href: '/items/5', role: null, testId: null }), 'unclassified');
});

test('matrixLabel: a native submit input with no other identity falls back to value (#52 regression)', () => {
  // <input type=submit value=Delete> with every other identity field null/absent — the label lives
  // ONLY in value. Before the fix this collapsed to '(unlabelled control)', defeating the reason
  // control-inventory.mjs captures value at all.
  const record = {
    text: null,
    ariaLabel: null,
    value: 'Delete',
    title: null,
    testId: null,
    name: null,
    href: null,
    className: null,
  };
  assert.equal(matrixLabel(record), 'Delete');
});

test('matrixLabel: precedence pin — ariaLabel outranks value (HTML-AAM accessible-name order)', () => {
  assert.equal(matrixLabel({ text: null, ariaLabel: 'Remove item', value: 'Delete' }), 'Remove item');
});

test('matrixLabel: text still outranks value', () => {
  assert.equal(matrixLabel({ text: 'Save', value: 'Submit' }), 'Save');
});

test('matrixLabel: value outranks title', () => {
  assert.equal(matrixLabel({ value: 'Delete', title: 'Delete this row' }), 'Delete');
});

test('extractRecord nulls value for <input type=image> (its value is submitted data, not a label)', async () => {
  // Per HTML-AAM an <input type=image>'s accessible name comes from aria-label/alt/title, never
  // `value` — its `value` attribute is SUBMITTED DATA (often a record id). Capturing it would leak
  // that payload into the console-logged inventory and, via matrixLabel's fallback chain, surface it
  // as the control's apparent label ahead of the real title-based label.
  const record = await extractRecord(
    stub({ type: 'image', value: 'record-42', title: 'Delete' }, { tag: 'INPUT', text: '' }),
  );
  assert.equal(record.value, null, "an image input's value must never be captured");
  assert.equal(record.title, 'Delete');
});

test('matrixLabel: an <input type=image> is labelled from title, never from its value payload (finding 2)', async () => {
  // Reproduces the finding: before the fix, VALUE_LABELLED_INPUT_TYPES did not exist and
  // BUTTON_LIKE_INPUT_TYPES (which includes 'image') gated value capture directly, so
  // value='record-42' was captured and outranked title in the matrixLabel fallback chain — surfacing
  // the submitted record id as the control's "label" instead of "Delete".
  const record = await extractRecord(
    stub({ type: 'image', value: 'record-42', title: 'Delete' }, { tag: 'INPUT', text: '' }),
  );
  assert.equal(matrixLabel(record), 'Delete', 'must fall back to title, not the (now null) value');
});

test('matrixLabel: a genuine <input type=submit value=Delete> still labels from value (no regression)', async () => {
  const record = await extractRecord(stub({ type: 'submit', value: 'Delete' }, { tag: 'INPUT', text: '' }));
  assert.equal(record.value, 'Delete');
  assert.equal(matrixLabel(record), 'Delete');
});

test('matrixLabel: a text-entry input (value null) with no other identity is unlabelled (no PII widening)', () => {
  const record = {
    text: null,
    ariaLabel: null,
    value: null,
    title: null,
    testId: null,
    name: null,
    href: null,
    className: null,
  };
  assert.equal(matrixLabel(record), '(unlabelled control)');
});

test('classifyByShape: camelCase / snake_case / kebab joins are tokenized and matched', () => {
  // \b-only matching would miss all of these (camelCase has no boundary; _ is a word char).
  assert.equal(classifyByShape({ text: '', title: null, ariaLabel: null, href: null, role: null, testId: 'deleteUser' }), 'candidate-destructive');
  assert.equal(classifyByShape({ text: '', title: null, ariaLabel: null, href: null, role: null, testId: 'remove_item' }), 'candidate-destructive');
  assert.equal(classifyByShape({ text: 'destroyAccount', title: null, ariaLabel: null, href: null, role: null, testId: null }), 'candidate-destructive');
  assert.equal(classifyByShape({ text: '', title: null, ariaLabel: null, href: '/users/7/delete-now', role: null, testId: null }), 'candidate-destructive');
  // A token that merely CONTAINS a verb substring must not match (deletedAt → token 'deleted').
  assert.equal(classifyByShape({ text: '', title: null, ariaLabel: null, href: null, role: null, testId: 'deletedAt' }), 'unclassified');
});
