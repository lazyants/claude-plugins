// enduser-handbook capture asset — non-normative reference implementation for the Playwright
// reference case. The normative, engine-agnostic contract lives in
// references/capture-spec-helpers.md (and capture-safety.md / page-identity.md).
// Engine-neutral: reused as-is by any engine's driver glue.
//
// dismiss-safe-label-policy.d.mts — TypeScript declarations for dismiss-safe-label-policy.mjs.

/** Default safe-negative dismiss labels (EN + DE), frozen. See dismiss-safe-label-policy.mjs. */
export declare const DEFAULT_SAFE_LABELS: readonly string[];

/** Verbs a safe negative label must never start with, frozen. See dismiss-safe-label-policy.mjs. */
export declare const UNSAFE_LEADING_VERBS: readonly string[];

/**
 * Decide whether `cancelLabel` is a safe fallback dismiss control. See
 * dismiss-safe-label-policy.mjs for the full allowlist + leading-verb contract.
 */
export declare function isSafeNegativeLabel(cancelLabel: string, safeLabels?: string[]): boolean;
