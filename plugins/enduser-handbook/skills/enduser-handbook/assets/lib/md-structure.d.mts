// enduser-handbook capture asset — non-normative reference implementation for the Playwright
// reference case. The normative, engine-agnostic contract lives in
// references/manifest-discipline.md, references/publish-targets/obsidian-vault.md,
// references/publish-targets/static-md.md, and references/revalidation.md (the D1-D6 design).
// Engine-neutral: reused as-is by any engine's driver glue and by capture.example.spec.ts.
//
// md-structure.d.mts — TypeScript declarations for md-structure.mjs so a downstream typechecking
// project resolves the .ts -> .mjs import. This repo does not compile TypeScript.

// A single heading in document order. ALL coordinates are 1-based line numbers; the body is the
// half-open interval [bodyStart, bodyEndExclusive). See md-structure.mjs for the full contract.
export interface HeadingNode {
  raw: string;
  title: string;
  depth: number;
  line: number;
  bodyStart: number;
  bodyEndExclusive: number;
}

export type SectionStatus = 'found' | 'needle-absent' | 'heading-absent';

/** See md-structure.mjs: the shared fence-aware masking primitive (character-offset- and line-position-preserving); the ONE JS fence implementation, reused by parseHeadings and the #258 citation scanner. */
export function maskFencedRegions(text: string): string;

/** See md-structure.mjs: the flat, document-order heading list; bodyEndExclusive is the next heading at depth <= this one, or lineCount + 1 at EOF (half-open [bodyStart, bodyEndExclusive)). */
export function parseHeadings(text: string): HeadingNode[];

/** See md-structure.mjs: the deepest heading whose body interval contains lineNumber, or null. */
export function findOwner(headings: HeadingNode[], lineNumber: number): HeadingNode | null;

/** See md-structure.mjs: the JS-side mirror of the bash 3-way section engine — 'heading-absent' (exit 2), 'needle-absent' (exit 1, empty needle included), or 'found' (exit 0). */
export function sectionStatus(text: string, headingRaw: string, needle: string): SectionStatus;
