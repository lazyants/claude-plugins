// enduser-handbook capture asset — non-normative reference implementation for the Playwright
// reference case. The normative, engine-agnostic contract lives in
// references/surface-diff.md.
// Engine-neutral: reused as-is by any engine's driver glue.
//
// surface-diff.d.mts — TypeScript declarations for surface-diff.mjs so a downstream project that DOES
// typecheck can resolve the .ts → .mjs import. This repo does not compile any TypeScript; these
// declarations exist purely so the reference impl is copyable into a typechecked project.

import type { NormalizedControl, ShapeHint } from './control-inventory.mjs';

/** One role's full inventory, as fed into diffSurfaces — one entry per per-role browser.newContext pass. */
export interface RoleInventory {
  role: string;
  controls: NormalizedControl[];
}

/** One row of the per-role matrix / diff: a distinct structural key with its per-role presence. */
export interface SurfaceDiffEntry {
  key: string;
  /** The structural key's tag component. */
  tag: string | null;
  /** The structural key's role component — the control's OWN DOM role attribute, not a perspective label. */
  role: string | null;
  name: string | null;
  testId: string | null;
  /** Display-only; inherits the audit's own PII boundary (control-inventory.mjs's matrixLabel). */
  label: string;
  /** 'candidate-destructive' if ANY contributing record under this key is. */
  shape: ShapeHint;
  /** Perspective role label -> count, 0-filled across EVERY role in `roles`. */
  presence: Record<string, number>;
  presentIn: string[];
  absentIn: string[];
}

export interface SurfaceDiff {
  roles: string[];
  /** Every distinct structural key across all roles, sorted by key. */
  matrix: SurfaceDiffEntry[];
  /** Subset of `matrix` where min(counts) !== max(counts) over ALL roles. */
  diff: SurfaceDiffEntry[];
}

/**
 * The structural, PII-free identity of a control: the tuple [tag, role, name, testId]. Never the
 * v1.0.6-broadened label/class fields (text/ariaLabel/title/className), nor href.
 */
export function structuralKey(
  record: Pick<NormalizedControl, 'tag' | 'role' | 'name' | 'testId'>,
): string;

/**
 * Diff the interactive surface across roles. Throws on a malformed entry (a non-string `role` or a
 * non-array `controls`) or on a duplicate role label.
 */
export function diffSurfaces(perRole: RoleInventory[]): SurfaceDiff;
