// Fixture for mask-and-assert.test.mjs — NOT a test file itself (the suite discovers only
// *.test.mjs, and this one must run under --experimental-strip-types because it imports the
// TypeScript driver).
//
// It drives the REAL maskAndAssert against a minimal DOM stub, so the refusals, their ORDER, and
// the mask/scan passes are exercised on the shipped artifact rather than on a re-implementation of
// them. Same shape as capture-guard-redirect-wiring.fixture.mjs: what is faked here is the ENGINE
// (a Locator, an element handle, and the DOM the handle's evaluate() runs against) — never the
// helper's own logic, all of which is the imported module's.
//
// WHY IT EXISTS: before #565, maskAndAssert had NO executable coverage at all — its guarantees were
// pinned only by greps in reference-assets.test.sh, which prove text is PRESENT and never that it
// WORKS. A grep cannot show the <canvas> refusal going red on a canvas and staying green on a
// legitimate capture, and that two-sided mutation is the whole claim #565 makes.
//
// WHAT THIS DOES NOT PROVE (state it, do not let the green imply it):
//   - real browser selector semantics. The shim's selector engine handles the exact forms the helper
//     and these fixtures use — a tag name, `*`, `.class`, and a comma-separated list of those.
//     `iframe, frame, object, embed` and `canvas` are covered exactly; a real engine's full CSS
//     grammar is not. Any OTHER form throws rather than answering, so this list cannot go stale
//     silently: adding a scenario that needs one fails loudly instead of matching nothing.
//   - CLOSED shadow roots, a canvas/frame painted over the rectangle from OUTSIDE the handed
//     subtree, or one attached AFTER the call. Those are documented as uncounted and stay uncounted.
//   - that a real <canvas> paints what the issue says it paints, or that page.screenshot composites
//     it. That is browser behaviour, corroborated by the measurement recorded on issue #565.
// The shim itself is guarded against a false GREEN by the `leak-scan-fires` and
// `coverage-assert-fires` scenarios below: both require the tree walk and the mask pass to have
// really run, so a shim that silently produced empty results could not pass them.

import { maskAndAssert } from '../skills/enduser-handbook/assets/capture-helpers.playwright.ts';

// ── Minimal DOM stub ────────────────────────────────────────────────────────────────────────────

/** One simple selector: `*`, a tag name, or `.class` — the only forms anything here asks for. */
function matchesSimple(el, sel) {
  const s = sel.trim();
  if (s === '*') return true;
  if (s.startsWith('.')) {
    const classAttr = el.getAttribute('class') || '';
    return classAttr.split(/\s+/).includes(s.slice(1));
  }
  // Anything that is not a bare tag name is a form this stub does not implement. THROW rather than
  // fall through to the tag compare: a selector the stub cannot evaluate but answers `false` to is a
  // silent false GREEN — the scenario passes while matching nothing, which is precisely the failure
  // this whole fixture exists to rule out. Loud is the only safe failure mode for an engine stub.
  if (!/^[a-zA-Z][a-zA-Z0-9-]*$/.test(s)) {
    throw new Error(
      `mask-and-assert fixture: unsupported selector form ${JSON.stringify(sel)}. The stub ` +
        "implements a tag name, '*', '.class', and comma-separated lists of those — extend it " +
        'deliberately rather than letting an unevaluatable selector answer "no match".',
    );
  }
  return el.tagName === s.toUpperCase();
}

const matchesAny = (el, selector) => selector.split(',').some((part) => matchesSimple(el, part));

class ShimNode {
  constructor() {
    this.parentNode = null;
  }
}

class ShimText extends ShimNode {
  constructor(value) {
    super();
    this.nodeValue = value;
  }
}

class ShimElement extends ShimNode {
  constructor(tagName, attrs = {}, children = []) {
    super();
    this.tagName = tagName.toUpperCase();
    this._attrs = new Map(Object.entries(attrs));
    this.childNodes = [];
    this.shadowRoot = null;
    this.ownerDocument = null;
    for (const child of children) this.append(child);
  }

  append(child) {
    child.parentNode = this;
    this.childNodes.push(child);
  }

  attachShadow(children) {
    this.shadowRoot = new ShimShadowRoot(this, children);
    return this.shadowRoot;
  }

  getAttribute(name) {
    return this._attrs.has(name) ? this._attrs.get(name) : null;
  }

  setAttribute(name, value) {
    this._attrs.set(name, String(value));
  }

  hasAttribute(name) {
    return this._attrs.has(name);
  }

  matches(selector) {
    return matchesAny(this, selector);
  }

  /** Light-DOM descendants only, document order — exactly what a real querySelectorAll returns. */
  querySelectorAll(selector) {
    const out = [];
    const walk = (node) => {
      for (const child of node.childNodes) {
        if (child instanceof ShimElement) {
          if (matchesAny(child, selector)) out.push(child);
          walk(child);
        }
      }
    };
    walk(this);
    return out;
  }

  // The getter is never read today — the helper only ASSIGNS textContent (that is the mask), and the
  // scan reads text through the tree walk. Kept anyway, for the same reason attachOwnerDocument and
  // the unused element constructors below are kept: a capability this stub LACKS fails SILENTLY
  // (`undefined`), which is the false-green direction. Unused DOM fidelity is cheap; a stub that
  // quietly answers `undefined` to a future read is not.
  get textContent() {
    let acc = '';
    for (const child of this.childNodes) {
      acc += child instanceof ShimText ? child.nodeValue : child.textContent;
    }
    return acc;
  }

  set textContent(value) {
    for (const child of this.childNodes) child.parentNode = null;
    this.childNodes = [];
    this.append(new ShimText(String(value)));
  }
}

/** A shadow root is the parentNode of its own children, so the helper's `.host` climb is exercised. */
class ShimShadowRoot extends ShimNode {
  constructor(host, children = []) {
    super();
    this.host = host;
    this.childNodes = [];
    this.ownerDocument = null;
    for (const child of children) {
      child.parentNode = this;
      this.childNodes.push(child);
    }
  }

  querySelectorAll(selector) {
    return ShimElement.prototype.querySelectorAll.call(this, selector);
  }
}

class ShimHTMLInputElement extends ShimElement {
  constructor(attrs = {}) {
    super('input', attrs);
    this.value = attrs.value ?? '';
  }
}

class ShimHTMLTextAreaElement extends ShimElement {
  constructor(attrs = {}, value = '') {
    super('textarea', attrs);
    this.value = value;
  }
}

class ShimHTMLOptionElement extends ShimElement {
  constructor(label, value) {
    super('option');
    this.text = label;
    this.value = value ?? label;
  }
}

class ShimHTMLSelectElement extends ShimElement {
  constructor(attrs = {}, options = []) {
    super('select', attrs, options);
    this.options = options;
    this.value = options.length > 0 ? options[0].value : '';
  }
}

const NodeFilterShim = { SHOW_TEXT: 0x4 };

/** Text-node walk over the light DOM, document order — no shadow piercing, as in a real TreeWalker. */
function createTreeWalkerShim(root, whatToShow) {
  const found = [];
  const walk = (node) => {
    for (const child of node.childNodes) {
      if (child instanceof ShimText) {
        if ((whatToShow & NodeFilterShim.SHOW_TEXT) !== 0) found.push(child);
      } else {
        walk(child);
      }
    }
  };
  walk(root);
  let i = 0;
  return { nextNode: () => (i < found.length ? found[i++] : null) };
}

const documentShim = { createTreeWalker: createTreeWalkerShim };

// The helper's browser closure names these as bare globals, exactly as it would in a page.
Object.assign(globalThis, {
  Element: ShimElement,
  ShadowRoot: ShimShadowRoot,
  HTMLInputElement: ShimHTMLInputElement,
  HTMLTextAreaElement: ShimHTMLTextAreaElement,
  HTMLSelectElement: ShimHTMLSelectElement,
  NodeFilter: NodeFilterShim,
  document: documentShim,
});

/** Give every node in the tree the ownerDocument the closure reads for createTreeWalker. */
function attachOwnerDocument(node) {
  node.ownerDocument = documentShim;
  for (const child of node.childNodes ?? []) {
    if (!(child instanceof ShimText)) attachOwnerDocument(child);
  }
  if (node.shadowRoot) attachOwnerDocument(node.shadowRoot);
}

// ── Builders ────────────────────────────────────────────────────────────────────────────────────

const el = (tag, attrs, children) => new ShimElement(tag, attrs ?? {}, children ?? []);
const txt = (value) => new ShimText(value);
const input = (attrs) => new ShimHTMLInputElement(attrs ?? {});
const option = (label, value) => new ShimHTMLOptionElement(label, value);
const select = (attrs, options) => new ShimHTMLSelectElement(attrs ?? {}, options ?? []);

/** A Locator whose elementHandle().evaluate(fn, args) runs `fn` against the shim tree. */
function fakeDialog(root) {
  attachOwnerDocument(root);
  return {
    elementHandle: async () => ({
      evaluate: async (fn, args) => fn(root, args),
    }),
  };
}

// ── Scenarios ───────────────────────────────────────────────────────────────────────────────────

const EMAIL = /[\w.+-]+@[\w-]+\.[\w.-]+/;

async function scenario(name, root, options) {
  let threw = null;
  try {
    // maskAndAssert reads nothing off its `page` argument; the dialog locator is the whole surface.
    await maskAndAssert(null, {
      placeholder: '••••••',
      patterns: [EMAIL],
      selectors: [],
      expectedCount: 0,
      ...options,
      dialog: fakeDialog(root),
    });
  } catch (err) {
    threw = err.message;
  }
  console.log(JSON.stringify({ name, threw }));
}

// 1. A plain masked region: the PII is listed, matched, and excluded from the scan. This is the
//    GREEN half of the canvas mutation — a legitimate capture must stay green after the refusal
//    lands.
const cleanRegion = () =>
  el('div', {}, [
    el('h2', {}, [txt('Customer details')]),
    el('span', { class: 'customer-email' }, [txt('jane@example.com')]),
    el('p', {}, [txt('Placed 3 orders')]),
  ]);

await scenario('clean-masked-region', cleanRegion(), {
  selectors: ['.customer-email'],
  expectedCount: 1,
});

// 2. The #565 case: a <canvas> in the region is refused by default.
await scenario(
  'canvas-refused',
  el('div', {}, [el('h2', {}, [txt('Invoice preview')]), el('canvas', { width: '800' })]),
  {},
);

// 3. …and the explicit opt-out is the way past it, mirroring allowUnscannedFrames.
await scenario(
  'canvas-opt-out',
  el('div', {}, [el('h2', {}, [txt('Invoice preview')]), el('canvas', { width: '800' })]),
  { allowUnscannedCanvas: true },
);

// 4. A canvas nested in an OPEN shadow root must be counted — the refusal reuses queryDeep, so a
//    regression that dropped the shadow walk would leave a web-component canvas silently uncounted.
const shadowCanvasRegion = () => {
  const host = el('preview-widget');
  host.attachShadow([el('canvas')]);
  return el('div', {}, [el('h2', {}, [txt('Preview')]), host]);
};
await scenario('canvas-in-open-shadow-root', shadowCanvasRegion(), {});

// 5. The region ITSELF being the canvas is counted by the .matches() term; querySelectorAll returns
//    descendants only, so without it a canvas-scoped capture would pass with the run green.
await scenario('region-is-canvas', el('canvas'), {});

// 6. Listing the <canvas> in `selectors` must NOT clear the refusal: setting textContent on a canvas
//    paints nothing, so the tag only removes it from the scan — the pixels are untouched.
await scenario(
  'canvas-listed-in-selectors-still-refused',
  el('div', {}, [el('canvas')]),
  { selectors: ['canvas'], expectedCount: 1 },
);

// 7. #472 regression: the frame refusal must still fire on its own.
await scenario('frame-still-refused', el('div', {}, [el('iframe', { src: '/preview' })]), {});

// 8. …and its opt-out must still work.
await scenario(
  'frame-opt-out',
  el('div', {}, [el('iframe', { src: '/preview' })]),
  { allowUnscannedFrames: true },
);

// 9. Both present: the frame refusal is checked first, so its message is the one reported.
await scenario('frame-and-canvas-reports-frame', el('div', {}, [el('iframe'), el('canvas')]), {});

// 10. The canvas refusal precedes the coverage assert, so a region with BOTH a canvas and a drifted
//    selector count is named as a canvas problem — "selector drift" would misdirect.
await scenario(
  'canvas-precedes-coverage-assert',
  el('div', {}, [el('canvas'), el('span', { class: 'customer-email' }, [txt('jane@example.com')])]),
  { selectors: ['.customer-email'], expectedCount: 7 },
);

// 11. SHIM SELF-CHECK: unmasked PII must still trip the leak scan. If the tree walk silently
//     collected nothing, every scenario above would pass for the wrong reason and this one would not
//     throw.
await scenario('leak-scan-fires', cleanRegion(), { selectors: [], expectedCount: 0 });

// 12. SHIM SELF-CHECK: the coverage assert must still fire on a count mismatch, which requires the
//     mask pass to have really matched and counted.
await scenario('coverage-assert-fires', cleanRegion(), {
  selectors: ['.customer-email'],
  expectedCount: 2,
});

// 13. The mask must reach a form control's value and a <select>'s option labels, and the masked
//     nodes must be excluded from the scan by identity — the behaviour the carve-out list describes.
await scenario(
  'form-controls-masked-and-excluded',
  el('div', {}, [
    input({ class: 'pii', value: 'jane@example.com', placeholder: 'jane@example.com' }),
    select({ class: 'pii' }, [option('jane@example.com'), option('bob@example.com')]),
  ]),
  { selectors: ['.pii'], expectedCount: 2 },
);

// 14. …and an UNMASKED control's value/placeholder/option labels are still scanned, so the exclusion
//     in 13 is by identity rather than by the scan having stopped reading controls at all.
await scenario(
  'unmasked-control-value-still-scanned',
  el('div', {}, [input({ value: 'jane@example.com' })]),
  { selectors: [], expectedCount: 0 },
);
