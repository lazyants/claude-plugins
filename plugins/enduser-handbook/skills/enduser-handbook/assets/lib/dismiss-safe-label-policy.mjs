// enduser-handbook capture asset — non-normative reference implementation for the Playwright
// reference case. The normative, engine-agnostic contract lives in
// references/capture-spec-helpers.md (and capture-safety.md / page-identity.md).
// Engine-neutral: reused as-is by any engine's driver glue.
//
// dismiss-safe-label-policy.mjs — the pure safe-negative-label gate used by dismissModal
// (capture-helpers.playwright.ts) to decide whether a fallback cancel/negative button label is safe
// to click when Escape does not close the dialog. Factored out so the allowlist + leading-verb
// guard is unit-tested without a browser, mirroring the capture-guard-policy.mjs extraction.
//
// Safe-negative dismiss labels are an ALLOWLIST, not a denylist. A denylist of dangerous verbs is
// the wrong tool here: "Cancel" is simultaneously the most common SAFE negative AND a substring of
// destructive labels ("Cancel subscription"), so it cannot be classified by verb alone. Instead the
// caller's cancelLabel must EXACTLY equal one of a small set of known-safe negatives (the default
// set below, extensible per project via isSafeNegativeLabel's safeLabels argument). Anything else —
// including a label that merely contains a commit/destructive verb — is refused. tokenize is still
// used to reject a multi-word label whose first token is a commit/destructive verb even if the
// author added it to safeLabels by mistake.

import { tokenize } from './capture-guard-policy.mjs';

/**
 * Default safe-negative dismiss labels (EN + DE). Exported as a FROZEN array — an immutable
 * snapshot no consumer can weaken, and whose exact contents a unit test can pin (catching an
 * accidental addition, not just a removal). Extend per project via isSafeNegativeLabel's
 * `safeLabels` argument; never by mutating this array.
 */
export const DEFAULT_SAFE_LABELS = Object.freeze([
  // EN
  'Cancel',
  'No',
  'Close',
  'Back',
  'Go back',
  'Keep',
  'Dismiss',
  'Not now',
  // DE
  'Abbrechen',
  'Nein',
  'Schließen',
  'Zurück',
  'Behalten',
]);

/**
 * Verbs a SAFE negative label must never start with (a primary/commit/destructive action). Frozen
 * for the same reason as DEFAULT_SAFE_LABELS — a closed, pinned set.
 */
export const UNSAFE_LEADING_VERBS = Object.freeze([
  'delete',
  'destroy',
  'remove',
  'disable',
  'deactivate',
  'approve',
  'send',
  'finalize',
  'revoke',
  'reset',
  'purge',
  'wipe',
  'confirm',
  'submit',
  'save',
  'löschen',
  'entfernen',
  'bestätigen',
  'senden',
  'speichern',
]);

// Derived lookup Set — kept PRIVATE (not exported). Object.freeze on a Set does not stop a consumer
// from mutating its CONTENTS the way a frozen array export does, so the exported immutable snapshot
// is the array above; this Set is only an internal derived structure for O(1) membership tests.
const UNSAFE_LEADING_VERB_SET = new Set(UNSAFE_LEADING_VERBS);

/**
 * Decide whether `cancelLabel` is a safe fallback dismiss control: it must EXACTLY match one of
 * DEFAULT_SAFE_LABELS ∪ safeLabels, AND its first token must not be a commit/destructive verb (so a
 * multi-word label mistakenly added to safeLabels — "Delete now" — is still refused). Exact match
 * only, no normalization: a case or trailing-whitespace variant of a safe label is NOT admitted.
 *
 * @param {string} cancelLabel
 * @param {string[]} [safeLabels]
 * @returns {boolean}
 */
export function isSafeNegativeLabel(cancelLabel, safeLabels = []) {
  const allowed = new Set([...DEFAULT_SAFE_LABELS, ...safeLabels]);
  if (!allowed.has(cancelLabel)) return false;
  const leadingToken = tokenize(cancelLabel)[0];
  if (leadingToken !== undefined && UNSAFE_LEADING_VERB_SET.has(leadingToken)) return false;
  return true;
}
