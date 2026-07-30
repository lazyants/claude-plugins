// enduser-handbook asset — non-normative reference implementation. The normative contract for the
// build-identity/provenance feature — W2's resolution order + run record, W5's completeness rule +
// chapter record, W6's report — lives in SKILL.md and in references/capture-engines.md,
// references/capture-safety.md and references/revalidation.md; this file is one Node-stdlib
// implementation of it, not a requirement (the resolution/validation logic could equally be
// reimplemented by a differently-engineered capture-record.mjs, so long as it agrees with the same
// prose contract). `capture.build_identity.command` is engine-agnostic — it runs the same way
// regardless of which capture engine drives `capture.command` (references/capture-engines.md).
//
// build-identity.mjs — the PURE, no-I/O half of the build-identity/provenance feature: identity-
// value normalization, the shared build_identity field-validity check, the resolution helpers W2's
// open/close sequence drives, the completeness/staleness comparison W5/W6 read against, delta
// classification, and the small rendering helper W6's report uses. Every exported function is a
// side-effect-free transform over plain data — no node:fs, no node:child_process, no dynamic
// import(), no `process` beyond what a pure module gets for free — so the whole resolution/
// validation surface is unit-testable (tests/build-identity.test.mjs) without a filesystem, a
// command executor or a browser. Side-effect-free is NOT total, and the difference is deliberate:
// exactly two functions throw a TypeError on an input outside their declared domain —
// `resolveBuildIdentity` on an unrecognized `uiObservation.kind`, and `classifyBuildDelta` when
// `record` is not a valid BuildIdentity (the usual cause being a whole chapter/run record passed
// where its `build_identity` field was meant). Those are caller-contract violations rather than
// the ordinary failures this module models, and every ordinary failure IS returned as data. The
// header previously said "total", which was simply false; a caller must not read it as a promise
// never to throw. The disk-touching half — hashing, the token/record lifecycle, the
// eight capture-record entrypoints — lives in the sibling module, assets/lib/capture-record.mjs,
// which imports the exports below rather than re-implementing them.
//
// Two things this file deliberately does NOT do, both intentional and both load-bearing for the
// purity claim above: it never runs `capture.build_identity.command` (that is an injected executor
// living in capture-record.mjs's `deps`), and it never performs the UI read (that is an LLM act —
// see resolveBuildIdentity's `needs_ui_read` return). Every function here only ever COMBINES
// already-obtained observations.

/**
 * @typedef {'command'|'ui'|'unavailable'} IdentitySource
 */

/**
 * @typedef {null
 *   |'no_source_configured'|'command_failed'|'command_output_rejected'
 *   |'ui_read_unavailable'|'ui_read_found_nothing'|'ui_read_rejected'
 *   |'build_changed_during_capture'|'build_unconfirmed'|'capture_failed'} ResolutionReason
 */

/**
 * @typedef {{value: string|null, source: IdentitySource, resolution_reason: ResolutionReason, detail: string|null}} BuildIdentity
 */

/**
 * Every value `resolution_reason` may hold once resolution has terminated (`null` means "a value
 * was obtained"). A dedicated test asserts every member here is produced by at least one fixture —
 * an enum member no fixture reaches is a branch that was never exercised.
 * @type {ReadonlyArray<Exclude<ResolutionReason, null>>}
 */
export const RESOLUTION_REASONS = Object.freeze([
  'no_source_configured',
  'command_failed',
  'command_output_rejected',
  'ui_read_unavailable',
  'ui_read_found_nothing',
  'ui_read_rejected',
  'build_changed_during_capture',
  'build_unconfirmed',
  'capture_failed',
]);

/** Every value `source` may hold on a valid `build_identity` sub-object. @type {ReadonlyArray<IdentitySource>} */
export const IDENTITY_SOURCES = Object.freeze(['command', 'ui', 'unavailable']);

/**
 * The fixed instruction returned alongside `needs_ui_read`, since the UI read is an LLM act with no
 * profile-configurable region — the prose step names WHERE to look, this module only says THAT a
 * look is needed.
 */
export const UI_READ_REGION_HINT =
  'the running application UI (footer, about/settings page, or wherever a version/build identifier is shown)';

// The identity grammar, applied to an already-canonicalized (trimmed/unquoted/newline-stripped)
// value. Leading char is alphanumeric only; the remainder additionally admits the closed set
// `. _ + : ~ ! /  -`. `!` exists to admit a PEP 440 epoch ("1!2.0"); `~` and `:` are otherwise unusual
// but appear in real version strings this feature must not reject. The class is CLOSED: nothing
// outside this exact set is ever accepted, at any position. Max 128 characters total (leading +127).
const IDENTITY_GRAMMAR = /^[A-Za-z0-9][A-Za-z0-9._+:~!/-]{0,127}$/;

/**
 * Normalize a raw identity string (typically `capture.build_identity.command`'s stdout, or the raw
 * text an LLM read off the running UI) into the canonical form stored in a record, or reject it.
 *
 * Order matters and is exactly: (1) strip AT MOST ONE trailing line terminator (`\n`, `\r\n`, or
 * `\r`) — stdout conventionally ends in exactly one, and a SECOND one left behind (e.g. `"4.3.1\n\n"`)
 * must still fail the grammar test below, not be silently absorbed; (2) strip all surrounding ASCII
 * space/tab; (3) strip one surrounding matched pair of ASCII double quotes (`npm pkg get version`
 * prints a JSON-quoted string) — an UNMATCHED leading or trailing quote is left in place and then
 * fails the grammar test, since a real quote character is never part of the allowed alphabet; then
 * (4) test the closed allowlist.
 *
 * `raw` must already be a string; a non-string is rejected outright rather than coerced (`String(raw)`
 * would happily stringify `431` into `"431"` and pass, hiding an executor that returned the wrong
 * shape).
 *
 * @param {unknown} raw
 * @returns {{ok: true, value: string}|{ok: false, reason: string}}
 */
export function normalizeBuildIdentity(raw) {
  if (typeof raw !== 'string') {
    return { ok: false, reason: 'not_a_string' };
  }

  let s = raw;

  // (1) at most one trailing line terminator — order matters: check the two-char CRLF form first so
  // a lone '\r' inside "...\r\n" is never mistaken for the whole terminator.
  if (s.endsWith('\r\n')) s = s.slice(0, -2);
  else if (s.endsWith('\n') || s.endsWith('\r')) s = s.slice(0, -1);

  // (2) surrounding ASCII space/tab only — never JS's `\s`/`trim()`, which also treats NBSP/BOM/
  // ideographic space as whitespace and would silently widen what this scan accepts.
  s = s.replace(/^[ \t]+/, '').replace(/[ \t]+$/, '');

  // (3) one surrounding MATCHED pair of double quotes. An unmatched quote is deliberately left in
  // the string so the grammar test below rejects it (a real quote is never in the allowed alphabet).
  if (s.length >= 2 && s.startsWith('"') && s.endsWith('"')) {
    s = s.slice(1, -1);
  }

  if (!IDENTITY_GRAMMAR.test(s)) {
    return { ok: false, reason: 'invalid_grammar' };
  }
  return { ok: true, value: s };
}

// Printable ASCII plus space — 0x20 ("space") through 0x7e ("~"). Every other scalar value,
// including every C0/C1 control, DEL (0x7f), and anything non-ASCII, is removed outright rather than
// escaped or replaced — `detail` is a diagnostic string, not a place to smuggle structure back in.
function isPrintableAscii(codePoint) {
  return codePoint >= 0x20 && codePoint <= 0x7e;
}

const DETAIL_MAX_LENGTH = 200;
const DETAIL_TRUNCATION_MARKER = '...';

/**
 * Sanitize a human-facing diagnostic string for the `detail` field: `detail` is NEVER authoritative
 * (nothing reads it to make a decision), so it is safe to be aggressive here.
 *
 * Removal runs first, over the FULL (unbounded-length) input, iterating by Unicode CODE POINT (a
 * `for...of` over the string, which recombines a well-formed surrogate pair into one step and still
 * visits a lone/unpaired surrogate code unit as itself) so a multi-byte character is never split in
 * a way that leaves half of it behind. Truncation to 200 characters — INCLUDING the literal `...`
 * marker when truncation actually happens — is applied only AFTER removal, to the already-cleaned
 * string. Doing it in the other order (truncate the raw text, then sanitize) can leave a
 * differently-sized result depending on how many invalid code points happened to fall in the
 * discarded tail — removal and truncation must not interact, so removal always runs to completion
 * first.
 *
 * @param {unknown} raw
 * @returns {string}
 */
export function sanitizeDetail(raw) {
  const s = typeof raw === 'string' ? raw : String(raw);
  let cleaned = '';
  for (const ch of s) {
    if (isPrintableAscii(ch.codePointAt(0))) cleaned += ch;
  }
  if (cleaned.length <= DETAIL_MAX_LENGTH) return cleaned;
  return cleaned.slice(0, DETAIL_MAX_LENGTH - DETAIL_TRUNCATION_MARKER.length) + DETAIL_TRUNCATION_MARKER;
}

function sanitizeDetailOrNull(raw) {
  return typeof raw === 'string' ? sanitizeDetail(raw) : null;
}

/**
 * @typedef {{kind: 'not_attempted'}
 *   |{kind: 'value', raw: unknown, detail?: string}
 *   |{kind: 'found_nothing', detail?: string}
 *   |{kind: 'unavailable', detail?: string}
 *   |{kind: 'rejected', detail?: string}} UiReadObservation
 */

/**
 * @typedef {{ok: true, raw: unknown, detail?: string}|{ok: false, detail?: string}} CommandOutcome
 */

// The three UI-read terminal kinds that map to a fixed reason, independent of any value. `value`
// is handled separately above (its reason depends on whether normalization succeeds).
const UI_TERMINAL_REASONS = {
  found_nothing: 'ui_read_found_nothing',
  unavailable: 'ui_read_unavailable',
  rejected: 'ui_read_rejected',
};

/**
 * W2's three-step resolution order for a SINGLE observation point (used identically for the
 * opening observation, the closing observation, and W6's current-identity resolution — "W6 resolves
 * the identity once, by the same three-step order W2 uses").
 *
 * 1. `commandOutcome` non-null and `ok` ⇒ normalize its `raw`; pass ⇒ `source: 'command'`.
 * 2. Otherwise, unless `uiReadEnabled === false` ⇒ examine `uiObservation`. `{kind:'not_attempted'}`
 *    (or a missing/null observation) is NOT terminal — it returns `{needs_ui_read, region_hint}` so
 *    the caller (a prose-driven stage) can perform the actual read and call again with a real
 *    observation. Every other kind is terminal: a valid `value` ⇒ `source: 'ui'`; an invalid
 *    `value.raw` ⇒ `ui_read_rejected` (never `needs_ui_read` again, and never recorded unvalidated);
 *    `found_nothing`/`unavailable`/`rejected` ⇒ their own fixed reason.
 * 3. Otherwise (`uiReadEnabled === false`, so the fallback never runs): `value: null`,
 *    `source: 'unavailable'`, and a reason describing WHY the command step alone ended resolution —
 *    `no_source_configured` when no command was configured at all, else the command's own failure
 *    reason.
 *
 * A `raw` that is not a string is rejected by `normalizeBuildIdentity`, never coerced — so
 * `{kind:'value', raw: 431}` terminates as `ui_read_rejected`, not as `"431"`.
 *
 * @param {{commandOutcome?: CommandOutcome|null, uiReadEnabled?: boolean, uiObservation?: UiReadObservation|null}} [input]
 * @returns {BuildIdentity|{needs_ui_read: true, region_hint: string}}
 */
export function resolveBuildIdentity({ commandOutcome = null, uiReadEnabled = true, uiObservation = null } = {}) {
  let commandFailureReason = null;
  let commandFailureDetail = null;

  if (commandOutcome != null) {
    if (commandOutcome.ok) {
      const normalized = normalizeBuildIdentity(commandOutcome.raw);
      if (normalized.ok) {
        return {
          value: normalized.value,
          source: 'command',
          resolution_reason: null,
          detail: sanitizeDetailOrNull(commandOutcome.detail),
        };
      }
      commandFailureReason = 'command_output_rejected';
    } else {
      commandFailureReason = 'command_failed';
    }
    commandFailureDetail = commandOutcome.detail ?? null;
  }

  if (uiReadEnabled === false) {
    return unavailableIdentity(commandFailureReason ?? 'no_source_configured', sanitizeDetailOrNull(commandFailureDetail));
  }

  if (uiObservation == null || uiObservation.kind === 'not_attempted') {
    return { needs_ui_read: true, region_hint: UI_READ_REGION_HINT };
  }

  if (uiObservation.kind === 'value') {
    const normalized = normalizeBuildIdentity(uiObservation.raw);
    if (normalized.ok) {
      return {
        value: normalized.value,
        source: 'ui',
        resolution_reason: null,
        detail: sanitizeDetailOrNull(uiObservation.detail),
      };
    }
    return unavailableIdentity('ui_read_rejected', sanitizeDetailOrNull(uiObservation.detail));
  }

  const reason = UI_TERMINAL_REASONS[uiObservation.kind];
  if (reason === undefined) {
    throw new TypeError(`resolveBuildIdentity: unrecognized uiObservation.kind: ${String(uiObservation.kind)}`);
  }
  return unavailableIdentity(reason, sanitizeDetailOrNull(uiObservation.detail));
}

// The shared shape for every terminal "no value obtained" outcome across both resolution functions
// below — `value` is always null and `source` always 'unavailable' on this path, by construction
// (see resolveClosingIdentity's own docstring on why a null value always pairs with
// 'unavailable'). `detail` is taken AS ALREADY SANITIZED — each call site remains responsible for
// running it through `sanitizeDetailOrNull`/`sanitizeDetail` itself first, since the two callers
// sanitize from different raw shapes (a CommandOutcome/UiReadObservation's own `.detail` here, vs.
// an already-sanitized BuildIdentity `.detail` or a freshly-derived diagnostic string in
// resolveClosingIdentity); this helper only assembles the four fields, it never sanitizes.
function unavailableIdentity(resolutionReason, detail) {
  return { value: null, source: 'unavailable', resolution_reason: resolutionReason, detail: detail ?? null };
}

function weakerSource(a, b) {
  // Neither side can be 'unavailable' here — both callers into weakerSource only reach it once
  // opening.value and closing.value are both known-non-null, which (by the field-validity rule
  // below) forces source to 'command' or 'ui' on both sides.
  return a === 'ui' || b === 'ui' ? 'ui' : 'command';
}

/**
 * Combine an already-resolved OPENING identity, the capture command's own outcome, and an already-
 * resolved CLOSING identity into the run's final recorded `build_identity` — the precedence table
 * W2 applies when `closeCaptureRun` commits the run record. Both `opening` and `closing` are
 * `resolveBuildIdentity` results (never the `needs_ui_read` shape — that must already have been
 * resolved by the time this runs).
 *
 * Precedence, in order — 0 outranks every observation outcome, and 1-4 are then mutually exclusive:
 *
 * 0. `captureOutcome.ok === false` ⇒ `capture_failed`, regardless of what either observation says —
 *    a run whose capture command did not complete cannot assert an identity for the assets it
 *    disturbed.
 * 1. the OPENING observation itself failed (`opening.value === null`) ⇒ record the opening's own
 *    reason and detail verbatim.
 * 2. opening succeeded but CLOSING failed, for ANY reason ⇒ `build_unconfirmed` — a closing failure
 *    is not evidence a deploy happened, but neither is it evidence one did not. The closing
 *    observation's own reason is carried in `detail` (sanitized), never in `resolution_reason`.
 * 3. both succeeded with DIFFERENT values ⇒ `build_changed_during_capture`.
 * 4. both succeeded with EQUAL values ⇒ that value, with `source` set to the WEAKER of the two —
 *    `'ui'` if either observation was `'ui'` — since a claim is only as strong as its weakest
 *    evidence.
 *
 * Whenever the final `value` is `null` (rules 0-3), `source` is always `'unavailable'` — never the
 * "weaker of command/ui" computation, which applies ONLY to rule 4's known, agreeing value. A
 * `null` value paired with a `'command'`/`'ui'` source is exactly the shape the shared field-
 * validity check below rejects, so this function never produces one.
 *
 * @param {{opening: BuildIdentity, captureOutcome: {ok: boolean}, closing: BuildIdentity}} input
 * @returns {BuildIdentity}
 */
export function resolveClosingIdentity({ opening, captureOutcome, closing }) {
  if (!captureOutcome?.ok) {
    return unavailableIdentity('capture_failed', null);
  }

  if (opening.value === null) {
    return unavailableIdentity(opening.resolution_reason, opening.detail);
  }

  if (closing.value === null) {
    return unavailableIdentity('build_unconfirmed', sanitizeDetail(closing.resolution_reason ?? ''));
  }

  if (opening.value !== closing.value) {
    return unavailableIdentity(
      'build_changed_during_capture',
      sanitizeDetail(`build changed during capture: ${opening.value} -> ${closing.value}`),
    );
  }

  const source = weakerSource(opening.source, closing.source);
  // Prefer the detail belonging to whichever side actually carries the recorded (weaker) source;
  // when both sides share one source, opening's detail wins (an arbitrary but stable choice —
  // `detail` is documented as never authoritative, so either side is an equally honest answer).
  const detailSource = closing.source === source && opening.source !== source ? closing : opening;
  return {
    value: opening.value,
    source,
    resolution_reason: null,
    detail: detailSource.detail ?? null,
  };
}

/**
 * The shared validity check for a `build_identity` sub-object — the ONLY checks the run-record and
 * chapter-record readers share (`chapters`/`opening`/`closing`/`asset_hashes` belong to the run
 * record alone; `asset_hashes` to the chapter record alone; neither is checked here). A record is
 * invalid when:
 *
 * - `value` is neither a string nor `null`;
 * - `value` is a string that does not pass `normalizeBuildIdentity` UNCHANGED — i.e. it is not
 *   already in canonical form. A reader that only type-checks `value` would accept a stored value
 *   carrying a control character or 300 characters, since the separately-correct normalizer would
 *   never see it;
 * - `source` is outside `IDENTITY_SOURCES`;
 * - `resolution_reason` is neither `null` nor a member of `RESOLUTION_REASONS`;
 * - `resolution_reason === null` while `source === 'unavailable'` (an unavailable resolution must
 *   name a reason), or `resolution_reason !== null` while a value was obtained (a resolved value
 *   carries no reason to explain);
 * - `value` and `source` disagree in either direction — `value !== null` requires `source` to be
 *   `'command'` or `'ui'`; `value === null` requires `source === 'unavailable'`;
 * - `detail` is `undefined` (absent, or explicitly set to `undefined`) — `BuildIdentity.detail` is a
 *   REQUIRED field (`string | null`, never omitted); every constructor of this shape in this module
 *   (`resolveBuildIdentity`, `resolveClosingIdentity`) always assigns it via `sanitizeDetailOrNull` or
 *   a literal, so a record reaching this check with `detail` missing did not come from either of
 *   them — it is corrupt or hand-edited, and this is the shared reader both record kinds rely on to
 *   catch that, not a legacy shape this module ever produced;
 * - `detail` is present and not `null`, and either not a string or not already in `sanitizeDetail`'s
 *   canonical (already-clean, already-bounded) form.
 *
 * @param {unknown} candidate
 * @returns {{ok: true}|{ok: false, reason: string}}
 */
export function isValidBuildIdentityField(candidate) {
  if (candidate === null || typeof candidate !== 'object' || Array.isArray(candidate)) {
    return { ok: false, reason: 'not_an_object' };
  }
  const { value, source, resolution_reason: resolutionReason, detail } = candidate;

  if (value !== null && typeof value !== 'string') {
    return { ok: false, reason: 'value_wrong_type' };
  }
  if (typeof value === 'string') {
    const normalized = normalizeBuildIdentity(value);
    if (!normalized.ok || normalized.value !== value) {
      return { ok: false, reason: 'value_not_canonical' };
    }
  }

  if (!IDENTITY_SOURCES.includes(source)) {
    return { ok: false, reason: 'source_invalid' };
  }

  if (resolutionReason !== null && !RESOLUTION_REASONS.includes(resolutionReason)) {
    return { ok: false, reason: 'resolution_reason_invalid' };
  }
  if (resolutionReason === null && source === 'unavailable') {
    return { ok: false, reason: 'resolution_reason_missing' };
  }
  if (resolutionReason !== null && value !== null) {
    return { ok: false, reason: 'resolution_reason_present_with_value' };
  }

  if (value !== null && source === 'unavailable') {
    return { ok: false, reason: 'value_source_mismatch' };
  }
  if (value === null && source !== 'unavailable') {
    return { ok: false, reason: 'value_source_mismatch' };
  }

  if (detail === undefined) {
    return { ok: false, reason: 'detail_missing' };
  }
  if (detail !== null) {
    if (typeof detail !== 'string') {
      return { ok: false, reason: 'detail_wrong_type' };
    }
    if (sanitizeDetail(detail) !== detail) {
      return { ok: false, reason: 'detail_not_canonical' };
    }
  }

  return { ok: true };
}

/**
 * The completeness/staleness comparison W6 (and, read-side, W5) runs a chapter's recorded
 * `asset_hashes` against its CURRENT embedded-image hashes. A SUBSET comparison, deliberately: the
 * record may hold an extra entry the chapter no longer embeds (re-authoring a chapter to drop one
 * screenshot does not make the claim false) — `ok` in that case. But every CURRENT embed must be
 * present in the map with a MATCHING hash, checked for every current entry (not just the first) so
 * a two-embed chapter where only the second changed is still caught. Zero current embeds is NEVER
 * `ok` — a vacuous `every()` over an empty set is not evidence of anything.
 *
 * @param {Record<string, string>} recordedHashes  asset-dir-relative POSIX path -> `sha256:` hash
 * @param {Record<string, string>} currentHashes  same shape, computed just now
 * @returns {{status: 'ok'}|{status: 'stale', reason: 'no_current_embeds'|'embed_missing_from_record'|'embed_hash_changed', path?: string}}
 */
export function verifyRecord(recordedHashes, currentHashes) {
  const currentKeys = Object.keys(currentHashes);
  if (currentKeys.length === 0) {
    return { status: 'stale', reason: 'no_current_embeds' };
  }
  for (const key of currentKeys) {
    if (!Object.hasOwn(recordedHashes, key)) {
      return { status: 'stale', reason: 'embed_missing_from_record', path: key };
    }
    if (recordedHashes[key] !== currentHashes[key]) {
      return { status: 'stale', reason: 'embed_hash_changed', path: key };
    }
  }
  return { status: 'ok' };
}

/**
 * @typedef {'absent'|'malformed'|'unsupported_version'|'stale'|'ok'} RecordState
 */

/**
 * Classify the delta between a chapter's CURRENT resolved identity and its recorded one. Record
 * state is checked BEFORE any value comparison, and outranks the current side's own resolution
 * reason — with no usable record the comparison is impossible regardless of whether the current
 * side resolved:
 *
 * - `recordState` is `'absent'`/`'malformed'`/`'unsupported_version'` ⇒ `indeterminate`,
 *   `classification_reason` is `'record_absent'`/`'record_malformed'`/`'record_unsupported_version'`
 *   respectively, and `recorded_source` is `null` (there is no record to have a source at all —
 *   `null` here is a different fact from `'unavailable'`, which means resolution failed INSIDE an
 *   existing record).
 * - `recordState === 'stale'` ⇒ `indeterminate`, `'record_stale'`, for BOTH an equal and an unequal
 *   value — staleness is a fact about which assets the record can vouch for, not about whether the
 *   two values happen to agree.
 * - `recordState === 'ok'` and the record's own `value` is `null` ⇒ `indeterminate`, and
 *   `classification_reason` is the RECORD's own stored `resolution_reason` — a valid, current,
 *   non-stale record whose value is `null` is its own axis, not a record-state failure, and its
 *   stored reason is the most specific fact available (it wins even when the current side is ALSO
 *   `null`).
 * - `recordState === 'ok'`, record known, and the CURRENT side's `value` is `null` ⇒
 *   `indeterminate`, and `classification_reason` is the CURRENT side's own `resolution_reason` —
 *   nothing about the record blocks the comparison here, only the current resolution's own failure.
 * - both known ⇒ `'unchanged'` (equal) or `'changed'` (unequal), `classification_reason: null`.
 *
 * Both sources appear on EVERY verdict, with their exact values, never a synthesized placeholder:
 * `current_source` is always `current.source`; `recorded_source` is `record.source` when
 * `recordState` is `'ok'` or `'stale'`, else `null`.
 *
 * When `recordState` is `'ok'` or `'stale'`, `record` is validated with `isValidBuildIdentityField`
 * and this THROWS a `TypeError` on a shape mismatch — e.g. the whole chapter/run record wrapper
 * passed instead of its `build_identity` field. Without this, `record.value` on a wrapper object is
 * `undefined` (not `null`), which slips past the null-value branch and produces a confidently WRONG
 * `'changed'`/`'unchanged'` verdict from comparing against `undefined` instead of erroring.
 *
 * @param {{current: BuildIdentity, recordState: RecordState, record: BuildIdentity|null}} input
 * @returns {{classification: 'unchanged'|'changed'|'indeterminate', classification_reason: string|null, current_source: IdentitySource, recorded_source: IdentitySource|null}}
 * @throws {TypeError} when `recordState` is `'ok'`/`'stale'` and `record` is not a valid BuildIdentity
 */
export function classifyBuildDelta({ current, recordState, record }) {
  const currentSource = current.source;

  if (recordState === 'absent' || recordState === 'malformed' || recordState === 'unsupported_version') {
    return {
      classification: 'indeterminate',
      classification_reason: `record_${recordState}`,
      current_source: currentSource,
      recorded_source: null,
    };
  }

  // recordState is 'stale' or 'ok' from here on, so `record` must be a real BuildIdentity — never
  // the chapter/run record WRAPPER it lives inside. A wrong shape must fail LOUDLY here rather than
  // silently misclassify: measured regression case, `record.value` on a wrapper object is
  // `undefined` (not `null`), which slips past the null-value branch below and lands on a
  // confidently WRONG 'changed'/'unchanged' verdict from comparing against `undefined` — the exact
  // silent-wrong-value failure mode this release's fail-closed philosophy exists to prevent
  // everywhere else. Reusing `isValidBuildIdentityField` rather than a bespoke check keeps this
  // module's one definition of "valid BuildIdentity" in one place.
  const validity = isValidBuildIdentityField(record);
  if (!validity.ok) {
    throw new TypeError(
      `classifyBuildDelta: record is not a valid BuildIdentity (${validity.reason}) — ` +
        'did you pass the whole chapter/run record instead of its build_identity field?',
    );
  }

  const recordedSource = record.source;

  if (recordState === 'stale') {
    return {
      classification: 'indeterminate',
      classification_reason: 'record_stale',
      current_source: currentSource,
      recorded_source: recordedSource,
    };
  }

  // recordState === 'ok'
  if (record.value === null) {
    return {
      classification: 'indeterminate',
      classification_reason: record.resolution_reason,
      current_source: currentSource,
      recorded_source: recordedSource,
    };
  }
  if (current.value === null) {
    return {
      classification: 'indeterminate',
      classification_reason: current.resolution_reason,
      current_source: currentSource,
      recorded_source: recordedSource,
    };
  }

  return {
    classification: current.value === record.value ? 'unchanged' : 'changed',
    classification_reason: null,
    current_source: currentSource,
    recorded_source: recordedSource,
  };
}

/**
 * Render a stored identity value for the operator-facing report — `null` (no value obtained, or no
 * record at all) renders as the word `unknown`; a real value renders as itself. Decision 3 lives
 * here rather than in frontmatter (frontmatter emission is deferred to 1.13.0).
 *
 * The sentinel word this function invents (`unknown`) is drawn from the SAME alphabet
 * `IDENTITY_GRAMMAR` accepts, so it is not actually reserved: a project whose version command prints
 * a literal `unknown` — or `none`, `n/a`, `null`, `N/A` — passes `normalizeBuildIdentity` and gets
 * recorded as a REAL, resolved value with those exact contents (measured directly against this
 * module: all five normalize successfully; only a bare `-` is rejected, and for the unrelated reason
 * that the grammar's leading character must be alphanumeric). So this function alone cannot tell a
 * row reporting "no identity was obtained" apart from a row reporting a real identity that happens to
 * read the same. The disambiguator lives OUTSIDE this function, in the adjacent `source` column the
 * caller renders next to it: a real `unknown` carries `source: 'command'` or `source: 'ui'`, while an
 * absent one carries `source: 'unavailable'` (see `buildProvenanceReport` in capture-record.mjs, which
 * always puts `value: formatIdentityValue(...)` next to a `source` field in the same row). A future
 * change that drops `source` from a report row using this helper, or reuses this helper somewhere
 * `source` is not also shown, silently breaks that assumption rather than visibly breaking it — this
 * function has no way to protect against that itself.
 *
 * @param {string|null} value
 * @returns {string}
 */
export function formatIdentityValue(value) {
  return value === null ? 'unknown' : value;
}
