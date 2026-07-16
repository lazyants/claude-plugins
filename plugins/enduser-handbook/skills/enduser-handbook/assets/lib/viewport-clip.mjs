// enduser-handbook capture asset — non-normative reference implementation for the Playwright
// reference case. The normative, engine-agnostic contract lives in
// references/capture-spec-helpers.md (and capture-safety.md / page-identity.md).
// Engine-neutral: reused as-is by any engine's driver glue.
//
// viewport-clip.mjs — the pure clip-geometry computation used by captureRegionClipped
// (capture-helpers.playwright.ts) to turn an element's bounding box into a single
// viewport-relative page.screenshot({ clip }) rectangle, with NO scroll-stitching. Kept separate
// from the Playwright driver so the geometry — the part most likely to hide an off-by-one — is
// unit-tested without a browser.

/**
 * Turn an element's viewport-relative bounding box into a `page.screenshot({ clip })` rectangle,
 * clamped to the viewport's vertical extent. `box` and `viewport` are both CSS px; `box.x`/`box.y`
 * MAY be negative (Playwright's `boundingBox()` reports coordinates relative to the viewport, so an
 * element scrolled above/left of it yields a negative origin).
 *
 * Throws on ANY horizontal clipping — `page.screenshot({ clip })` silently CROPS a clip rectangle
 * that extends past the viewport, hiding real content with no signal, and a caller who scrolled the
 * element fully into view (per captureRegionClipped's contract) should never legitimately hit this;
 * a horizontal overflow means the element is wider than the viewport. Also throws on an empty
 * vertical intersection (the element is entirely above or below the viewport after clamping) — that
 * is not a clip, it is nothing to shoot.
 *
 * Vertical overflow alone is NOT an error: the returned rectangle is clamped to the visible height
 * and `fitsFullHeight` reports whether the FULL element fit. `fitsFullHeight: false` is the
 * expected, disclosed case — the caller/doc discloses the truncated remainder in prose.
 *
 * @param {{x: number, y: number, width: number, height: number}} box
 * @param {{width: number, height: number}} viewport
 * @returns {{x: number, y: number, width: number, height: number, fitsFullHeight: boolean}}
 */
export function clampClipToViewport(box, viewport) {
  if (box.x < 0 || box.x + box.width > viewport.width) {
    throw new Error(
      `clampClipToViewport: horizontal clip — box.x=${box.x}, box.width=${box.width} does not fit ` +
        `within viewport.width=${viewport.width}. Scroll/resize so the element is fully within the ` +
        'viewport horizontally; this helper refuses to silently crop a clip rectangle.',
    );
  }

  const y = Math.max(0, box.y);
  const height = Math.min(box.y + box.height, viewport.height) - y;
  if (height <= 0) {
    throw new Error(
      `clampClipToViewport: empty vertical intersection — box.y=${box.y}, box.height=${box.height} ` +
        `does not overlap viewport.height=${viewport.height} at all.`,
    );
  }

  const fitsFullHeight = box.y >= 0 && box.y + box.height <= viewport.height;
  return { x: box.x, y, width: box.width, height, fitsFullHeight };
}
