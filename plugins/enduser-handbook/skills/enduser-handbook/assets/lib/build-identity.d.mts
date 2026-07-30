// enduser-handbook asset — non-normative reference implementation. The normative contract for the
// build-identity/provenance feature — W2's resolution order + run record, W5's completeness rule +
// chapter record, W6's report — lives in SKILL.md and in references/capture-engines.md,
// references/capture-safety.md and references/revalidation.md.
//
// build-identity.d.mts — TypeScript declarations for build-identity.mjs so a downstream
// typechecking project resolves the .ts → .mjs import. This repo does not compile TypeScript.

/** Where a resolved identity value came from. */
export type IdentitySource = 'command' | 'ui' | 'unavailable';

/** Why resolution ended without a value; `null` means a value WAS obtained. */
export type ResolutionReason =
  | null
  | 'no_source_configured'
  | 'command_failed'
  | 'command_output_rejected'
  | 'ui_read_unavailable'
  | 'ui_read_found_nothing'
  | 'ui_read_rejected'
  | 'build_changed_during_capture'
  | 'build_unconfirmed'
  | 'capture_failed';

/** A resolved (or explicitly unresolved) build identity, the shape stored in both record kinds. */
export interface BuildIdentity {
  value: string | null;
  source: IdentitySource;
  resolution_reason: ResolutionReason;
  detail: string | null;
}

/** Every member `resolution_reason` may hold (excluding `null`). */
export declare const RESOLUTION_REASONS: ReadonlyArray<Exclude<ResolutionReason, null>>;

/** Every member `source` may hold on a valid `build_identity` sub-object. */
export declare const IDENTITY_SOURCES: ReadonlyArray<IdentitySource>;

/** The fixed instruction returned alongside `needs_ui_read`. */
export declare const UI_READ_REGION_HINT: string;

/**
 * Normalize a raw identity string into its canonical stored form, or reject it. See the .mjs file
 * header for the exact strip order (line terminator, then space/tab, then a matched quote pair) and
 * the closed allowlist grammar. `raw` must already be a string; a non-string is rejected, not
 * coerced.
 */
export declare function normalizeBuildIdentity(
  raw: unknown,
): { ok: true; value: string } | { ok: false; reason: string };

/**
 * Sanitize a human-facing `detail` string: strips every non-printable-ASCII/non-space code point
 * (over the FULL input, by code point), then truncates to 200 characters INCLUDING a literal `...`
 * marker when truncation happens. `detail` is never authoritative.
 */
export declare function sanitizeDetail(raw: unknown): string;

/** The command executor's already-obtained result — this module never runs the command itself. */
export type CommandOutcome = { ok: true; raw: unknown; detail?: string } | { ok: false; detail?: string };

/**
 * The UI-read observation union. Only `not_attempted` may still need a read; every other kind is
 * terminal and maps to its own `ResolutionReason`.
 */
export type UiReadObservation =
  | { kind: 'not_attempted' }
  | { kind: 'value'; raw: unknown; detail?: string }
  | { kind: 'found_nothing'; detail?: string }
  | { kind: 'unavailable'; detail?: string }
  | { kind: 'rejected'; detail?: string };

/**
 * W2's three-step resolution order (command → UI fallback → unavailable) for a single observation
 * point. Used identically for the opening observation, the closing observation, and W6's current-
 * identity resolution. Returns `{needs_ui_read, region_hint}` only when `uiReadEnabled` is not
 * `false` and the UI has not yet been attempted (`uiObservation` absent or `{kind:'not_attempted'}`)
 * — the caller performs the read and calls again with a real observation.
 */
export declare function resolveBuildIdentity(input?: {
  commandOutcome?: CommandOutcome | null;
  uiReadEnabled?: boolean;
  uiObservation?: UiReadObservation | null;
}): BuildIdentity | { needs_ui_read: true; region_hint: string };

/**
 * Combine an already-resolved OPENING identity, the capture command's own outcome, and an already-
 * resolved CLOSING identity into the run's final recorded `build_identity`, per the 0-4 precedence
 * table documented on the .mjs export (capture_failed outranks everything; an opening failure keeps
 * its own reason; any closing failure after a successful opening is `build_unconfirmed`; a changed
 * value is `build_changed_during_capture`; an equal value records the WEAKER of the two sources).
 */
export declare function resolveClosingIdentity(input: {
  opening: BuildIdentity;
  captureOutcome: { ok: boolean };
  closing: BuildIdentity;
}): BuildIdentity;

/**
 * The shared validity check for a `build_identity` sub-object. It validates that sub-object and
 * nothing else of either record's shape — the surrounding `record_version`/`run_id`/`asset_hashes`
 * fields are each checked by the record readers themselves.
 *
 * [round 6] Three production call sites, not two: both record readers (capture-record.mjs —
 * `readRunRecordText` for the run record, `readChapterRecordText` for the chapter record) AND
 * `classifyBuildDelta` in this same module (build-identity.mjs:521), which reuses it to fail loudly
 * when `recordState` is `'ok'`/`'stale'` and `record` is not a real `BuildIdentity` — see that
 * function's own comment for why. The previous wording ("run by both record readers ... and nothing
 * else") was ambiguous between "nothing else RUNS it" — false, `classifyBuildDelta` does — and
 * "nothing else of the record's shape is validated BY it", which is what was meant; it is now
 * stated so only the true reading is available.
 */
export declare function isValidBuildIdentityField(candidate: unknown): { ok: true } | { ok: false; reason: string };

/**
 * Subset staleness comparison: every CURRENT embed must appear in `recordedHashes` with a matching
 * hash; an extra entry in `recordedHashes` the chapter no longer embeds is fine. Zero current embeds
 * is never `ok`.
 *
 * [round 6] Split per `reason` rather than one stale member with an optional `path`, because `path`
 * is not optional — it is reason-determined, and measured so on every branch: `no_current_embeds`
 * returns before any key is examined and so can NAME no path, while `embed_missing_from_record` and
 * `embed_hash_changed` are both reached from inside the key loop and always carry the offending
 * key. The old `path?: string` forced a caller handling either of the latter two to write a
 * null-check that can never fire. Pure narrowing of what the runtime already returns — no behavior
 * change.
 */
export declare function verifyRecord(
  recordedHashes: Record<string, string>,
  currentHashes: Record<string, string>,
):
  | { status: 'ok' }
  | { status: 'stale'; reason: 'no_current_embeds' }
  | { status: 'stale'; reason: 'embed_missing_from_record'; path: string }
  | { status: 'stale'; reason: 'embed_hash_changed'; path: string };

/** The record-state axis `classifyBuildDelta` checks before any value comparison. */
export type RecordState = 'absent' | 'malformed' | 'unsupported_version' | 'stale' | 'ok';

/**
 * Classify the delta between a chapter's current resolved identity and its recorded one. See the
 * .mjs export for the full precedence (record state before any value comparison, and outranking the
 * current side's own resolution reason). THROWS a `TypeError` when `recordState` is `'ok'`/`'stale'`
 * and `record` is not a valid `BuildIdentity` — e.g. the whole chapter/run record wrapper passed
 * instead of its `build_identity` field.
 */
export declare function classifyBuildDelta(input: {
  current: BuildIdentity;
  recordState: RecordState;
  record: BuildIdentity | null;
}): {
  classification: 'unchanged' | 'changed' | 'indeterminate';
  classification_reason: string | null;
  current_source: IdentitySource;
  recorded_source: IdentitySource | null;
};

/**
 * Render a stored identity value for the report: `null` renders as the word `unknown`. That word is
 * not reserved — `unknown`/`none`/`n/a`/`null`/`N/A` are all grammar-legal values a real command can
 * emit, so this function alone cannot distinguish "no identity obtained" from a real identity that
 * happens to read the same; the caller's adjacent `source` field is the disambiguator. See the .mjs
 * export's docstring for the measured detail.
 */
export declare function formatIdentityValue(value: string | null): string;
