// enduser-handbook capture asset — non-normative reference implementation for the Playwright
// reference case. The normative, engine-agnostic contract lives in
// references/capture-spec-helpers.md (and capture-safety.md / page-identity.md).
// Engine-neutral: reused as-is by any engine's driver glue.
//
// viewport-clip.d.mts — TypeScript declarations for viewport-clip.mjs.

/**
 * Turn an element's viewport-relative bounding box into a `page.screenshot({ clip })` rectangle,
 * clamped to the viewport's vertical extent. Throws on any horizontal clipping or an empty vertical
 * intersection. See viewport-clip.mjs for the full contract.
 */
export declare function clampClipToViewport(
  box: { x: number; y: number; width: number; height: number },
  viewport: { width: number; height: number },
): { x: number; y: number; width: number; height: number; fitsFullHeight: boolean };
