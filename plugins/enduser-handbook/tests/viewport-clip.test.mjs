// Unit tests for the pure clip-geometry computation used by captureRegionClipped
// (capture-helpers.playwright.ts). Zero deps — runs under Node's built-in test runner:
// `node --test viewport-clip.test.mjs`.
//
// These pin the two hard invariants a naive "just clip whatever boundingBox() got" implementation
// would silently violate: ANY horizontal clip must throw (page.screenshot({ clip }) crops silently
// otherwise, hiding real content with no signal), and an empty vertical intersection (the element
// entirely above/below the viewport) must throw rather than return a degenerate 0/negative-height
// rectangle. Vertical overflow alone is NOT an error — it clamps and reports fitsFullHeight: false.

import test from 'node:test';
import assert from 'node:assert/strict';

import { clampClipToViewport } from '../skills/enduser-handbook/assets/lib/viewport-clip.mjs';

const VP = { width: 1280, height: 800 };

// A naive "just pass the box through, no clamp, no throw" implementation — stands in for the
// regression these throw-case tests must catch: RED-before-green against this naive passthrough.
function naivePassthrough(box) {
  return { x: box.x, y: box.y, width: box.width, height: box.height, fitsFullHeight: true };
}

test('element fully inside the viewport: no clip, fitsFullHeight true', () => {
  const box = { x: 100, y: 100, width: 200, height: 200 };
  assert.deepEqual(clampClipToViewport(box, VP), { x: 100, y: 100, width: 200, height: 200, fitsFullHeight: true });
});

test('element taller than the viewport by 120px: height clamped, fitsFullHeight false', () => {
  const box = { x: 0, y: 0, width: 1280, height: 920 }; // viewport height (800) + 120
  assert.deepEqual(clampClipToViewport(box, VP), { x: 0, y: 0, width: 1280, height: 800, fitsFullHeight: false });
});

test('negative-y (scrolled above the viewport): top clamps to 0, height reduced, fitsFullHeight false', () => {
  const box = { x: 0, y: -50, width: 1280, height: 300 };
  assert.deepEqual(clampClipToViewport(box, VP), { x: 0, y: 0, width: 1280, height: 250, fitsFullHeight: false });
});

test('negative-x throws (horizontal clip is never silently cropped)', () => {
  const box = { x: -10, y: 0, width: 200, height: 200 };
  assert.throws(() => clampClipToViewport(box, VP), /horizontal clip/);
  // Red-before-green: a naive passthrough does NOT throw — proving this case would regress silently
  // without the assertion above.
  assert.doesNotThrow(() => naivePassthrough(box));
});

test('right-overflow (box.x + box.width > viewport.width) throws', () => {
  const box = { x: 1200, y: 0, width: 200, height: 200 };
  assert.throws(() => clampClipToViewport(box, VP), /horizontal clip/);
  assert.doesNotThrow(() => naivePassthrough(box));
});

test('wider-than-viewport (box.x = 0 but box.width alone overflows) throws', () => {
  const box = { x: 0, y: 0, width: 1300, height: 200 };
  assert.throws(() => clampClipToViewport(box, VP), /horizontal clip/);
  assert.doesNotThrow(() => naivePassthrough(box));
});

test('exact-edge (x=0, x+width=viewport.width): no throw, full width kept', () => {
  const box = { x: 0, y: 0, width: 1280, height: 200 };
  assert.deepEqual(clampClipToViewport(box, VP), { x: 0, y: 0, width: 1280, height: 200, fitsFullHeight: true });
});

test('one-pixel-inside the right edge: no throw', () => {
  const box = { x: 1, y: 0, width: 1278, height: 200 };
  assert.deepEqual(clampClipToViewport(box, VP), { x: 1, y: 0, width: 1278, height: 200, fitsFullHeight: true });
});

test('empty intersection (box.y >= viewport.height) throws', () => {
  const box = { x: 0, y: 850, width: 200, height: 100 };
  assert.throws(() => clampClipToViewport(box, VP), /empty vertical intersection/);
  assert.doesNotThrow(() => naivePassthrough(box));
});
