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
