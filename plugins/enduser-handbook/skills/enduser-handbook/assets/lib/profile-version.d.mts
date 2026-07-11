// enduser-handbook asset — non-normative reference implementation. The normative contract for the
// profile_version pre-flight scan lives in references/profile-validation.md.
//
// profile-version.d.mts — TypeScript declarations for profile-version.mjs.

/** The only profile_version this release understands. */
export declare const CURRENT_PROFILE_VERSION: 1;

/** Every profile_version readProfileVersion accepts as 'ok'. */
export declare const SUPPORTED_PROFILE_VERSIONS: number[];

/**
 * Extension point for a future cross-version migration: { [from]: { to, instructions } }. Empty —
 * only v1 ships, so no cross-version migration exists yet.
 */
export declare const MIGRATIONS: Record<number, { to: number; instructions: string }>;

/** The verdict returned by readProfileVersion. */
export interface ProfileVersionVerdict {
  status: 'ok' | 'unsupported' | 'missing' | 'duplicate' | 'malformed';
  version: number | null;
  message: string;
}

/**
 * Scan RAW profile text for the column-0, top-level `profile_version:` mapping key. A pre-flight
 * version reader, not a YAML validator — see references/profile-validation.md for the exact
 * recognized shape and the honest invariant it upholds. Never throws, for any input.
 */
export declare function readProfileVersion(rawText: unknown): ProfileVersionVerdict;

/** The verdict returned by scanStructure — always the malformed shape, since a clean scan is null. */
export interface StructuralVerdict {
  status: 'malformed';
  version: null;
  message: string;
}

/**
 * Cross-line structural guard (issue #110): scans the same line-split RAW profile text for an
 * unterminated flow collection, an unterminated quoted scalar, or an alias to an anchor that does not
 * exist anywhere in the document. NOT a YAML parser and not exhaustive — see
 * references/profile-validation.md for the mechanisms covered and the honest residual. Returns null
 * when nothing structural is found (the document may still be invalid for a reason outside this
 * scan's scope). False-reject-free by design. Never throws.
 */
export declare function scanStructure(lines: string[]): StructuralVerdict | null;
