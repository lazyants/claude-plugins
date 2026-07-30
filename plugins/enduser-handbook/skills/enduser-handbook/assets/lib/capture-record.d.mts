// enduser-handbook capture asset — non-normative reference implementation of the build-provenance
// disk layer. The normative contract lives in SKILL.md (W1/W2/W5/W6) and row6-generated.md (row
// 6's state table, signature rows, ledger row and test matrix, generated from ROW6-TRANSITIONS).
//
// capture-record.d.mts — TypeScript declarations for capture-record.mjs so a downstream
// typechecking project resolves the .ts -> .mjs import. This repo does not compile TypeScript.

import type { BuildIdentity, CommandOutcome, UiReadObservation } from './build-identity.d.mts';
import type { CaptureProfileLike, ChapterEntry, ExpectedAssetsResult } from './chapter-paths.d.mts';

// [round 5, codex finding 5] The `deps.expectedAssets` override every one of `recordChapterProvenance`
// and `buildProvenanceReport`'s runtime bodies reads (capture-record.mjs: `deps?.expectedAssets ??
// expectedAssets`) — measured, both call sites invoke it with the IDENTICAL six-argument shape
// chapter-paths.mjs's own `expectedAssets` accepts, so one alias covers both rather than two
// independently-drifting inline signatures. Deliberately NOT a `CaptureRecordDeps` member (see that
// interface's own doc comment): unlike every field there, this one is read off the RAW `deps`
// argument rather than the `mergeDeps`-merged one, defaults inline via `??` rather than through
// `defaultDeps`, and only these two entrypoints ever read it — the other eight exported functions'
// `deps?: Partial<CaptureRecordDeps>` seam never sees it, so folding it into that interface would
// wrongly imply all ten do.
export type ExpectedAssetsOverride = (
  profileLike: CaptureProfileLike,
  entry: ChapterEntry,
  chapterFile: string,
  chapterText: string,
  filenames: string[],
  target: string,
) => ExpectedAssetsResult;

// [round 3, codex] `slug`/`group` were previously typed `string | number` / `string | null` — but
// gate 1 (validateEntriesForCapture, run at the top of open/W5/W6) now deliberately REJECTS a
// non-string slug and a non-string group (a numeric slug or a null group used to silently coerce
// via `String(...)` before the type check ever saw the original value — IMPORTANT 1, round 2 of
// this review). A TypeScript caller following the OLD, wider type could construct an entry this
// runtime now halts on with `invalid_slug`/`invalid_group`; the type must describe what is actually
// ACCEPTED, not merely what the JS implementation can survive typing errors on without crashing.
export interface ChapterEntryLike {
  slug: string;
  group?: string;
}

export interface ProfileLike {
  capture: {
    output_dir: string;
    build_identity?: { command?: string; ui_read?: boolean } | null;
  };
  publish: {
    chapters_dir: string;
    // [round 6] REQUIRED, not optional. `assets/profile.schema.json` lists `target` in
    // `publish.required`, so a schema-conforming profile always carries it and this runtime can
    // never legitimately observe `undefined` — the previous `target?: string` described a profile
    // the schema already rejects. It matters because the value is not merely stored: capture-record.mjs
    // passes it straight through to chapter-paths' `expectedAssets` — read in
    // `recordChapterProvenance` and handed to the extractor there, and read inline at the
    // equivalent call in `buildProvenanceReport` — whose own
    // declaration types the parameter `target: string`, so the optional spelling let a
    // TypeScript caller construct a profile that type-checks here and violates the contract
    // there. Measured, that is not a cosmetic disagreement: with a chapter file inside its own
    // asset dir, `buildEmbedCandidates` yields ["shot.png","../a/shot.png"] for
    // `target: 'static_md'` and only ["shot.png"] for `undefined`, so a chapter embedding the
    // retained legacy static_md spelling goes from `{ok:true,assets:[...]}` to
    // `{ok:false,halt:{construct:"unmatched image destination '../a/shot.png'"}}`. The schema is
    // what makes the requirement real; this declaration can only describe it, never enforce it.
    target: string;
  };
}

export interface Halt {
  halt: string;
  message?: string;
  [key: string]: unknown;
}

export type HaltResult = { ok: false; halts: Halt[] };
export type NeedsUiRead = { needs_ui_read: true; region_hint: string };

/**
 * See capture-record.mjs: RFC 8785 (JCS) canonicalization of an in-memory JS value.
 *
 * [round 6] PARTIAL, despite the total-looking result union — this function does not return on
 * every `unknown`. The private `canonicalizeJcsValue`, which recurses into itself for each object
 * key, carries neither a cycle guard nor a depth bound, so a self-referential value or a deep one
 * exits by THROWING a `RangeError: Maximum call stack size exceeded` rather than by returning
 * `{ok:false, reason}`. Measured: a `{self: <cycle>}` and a 20 000-deep nesting both throw, while
 * `undefined`/`NaN`/`bigint`/function inputs correctly return `undefined_unsupported` /
 * `non_finite_number` / `bigint_unsupported` / `unsupported_value_type`. Documented rather than
 * guarded on purpose: issue #381 tracks splitting this canonicalizer out, and that is where a
 * structural fix belongs — this release does not widen the runtime for it. No caller inside this
 * module can reach the throw (every payload it canonicalizes is built internally from a build
 * identity and an asset-hash map), but the export is offered over `unknown`, so a caller writing
 * `const r = jcsCanonicalize(x); if (!r.ok) ...` must still guard the call itself.
 */
export function jcsCanonicalize(value: unknown): { ok: true; canonical: string } | { ok: false; reason: string };

/** See capture-record.mjs: SHA-256 of the UTF-8 bytes of an already-canonicalized string, hex-encoded. */
export function sha256HexOfCanonical(canonical: string): string;

/**
 * See capture-record.mjs: the `sha256:`-prefixed digest of an opening payload's canonical form.
 * Throws on an uncanonicalizable payload — an `Error` naming the reason
 * (`digestOpeningPayload: cannot canonicalize opening payload (undefined_unsupported)`, measured).
 * [round 6] It also inherits `jcsCanonicalize`'s own partiality, and that case is NOT the `Error`
 * this sentence describes: a cyclic or deeply-nested payload propagates a `RangeError` from the
 * canonicalizer instead (measured). See `jcsCanonicalize` above for why that is documented rather
 * than fixed here.
 */
export function digestOpeningPayload(openingPayload: unknown): string;

/** See capture-record.mjs: field-by-field validation of a run record's raw JSON text (duplicate-key- and lone-surrogate-aware). */
export function readRunRecordText(text: string): { ok: true; record: RunRecord } | { ok: false; reason: string };

/** A record whose `record_version` this reader does not understand (not `1`) — read back MINIMALLY: only the version field itself is confirmed to be a well-formed integer >= 1. Every OTHER field is UNVALIDATED and may be absent, malformed, or of a shape a future version invented; do not assume any `ChapterRecord` field is present. */
export interface UnsupportedVersionChapterRecord {
  record_version: number;
  [key: string]: unknown;
}

export type ChapterRecordReadResult =
  | { ok: true; record: ChapterRecord }
  | { ok: true; record: UnsupportedVersionChapterRecord; unsupportedVersion: true }
  | { ok: false; reason: string };

/** See capture-record.mjs: field-by-field validation of a chapter record's raw JSON text. A `record_version` other than `1` is NOT validated against v1's field rules (that would be meaningless for a version this reader was not written for) and is returned as `{ok: true, record, unsupportedVersion: true}` — the runtime's actual behavior, which the v1-only `ChapterRecord` shape below cannot describe on its own. */
export function readChapterRecordText(text: string): ChapterRecordReadResult;

export interface RunRecord {
  record_version: 1;
  run_id: string;
  opening_digest: string;
  build_identity: BuildIdentity;
  chapters: Record<string, { opening: Record<string, string>; closing: Record<string, string> }>;
}

export interface ChapterRecord {
  record_version: 1;
  run_id: string;
  build_identity: BuildIdentity;
  asset_hashes: Record<string, string>;
  detail?: string;
}

/** See capture-record.mjs: `<publish.chapters_dir>/.provenance` — the plugin-owned provenance root. */
export function provenanceRoot(profileLike: ProfileLike): string;

/** See capture-record.mjs: `<root>/chapters/<group>/<slug>.json` (grouped) or `<root>/chapters/<slug>.json` (flat) — stable across W2, W5 and W6. */
export function chapterRecordPath(profileLike: ProfileLike, entry: ChapterEntryLike): string;

export type OwnershipResult =
  | { ok: true; root: string }
  | { ok: false; halts: Halt[] }
  | { ok: false; skip: true; warnings: string[] };

/** See capture-record.mjs: ledger row 1 — gate 5, the provenance-root/capture.output_dir disjointness check. Called from W1 prose and, silently, at the top of every other entrypoint. */
export function assertProvenanceOwnership(profileLike: ProfileLike, deps?: Partial<CaptureRecordDeps>): OwnershipResult;

// A discriminated union, not one interface with every payload field optional — `skipped` is the
// discriminant. A skipped run (W1's ownership outcome) carries ONLY `skipped: true`: see
// capture-record.mjs's `openCaptureRun`/`closeCaptureRun`, both of which return exactly
// `{runState: {skipped: true}}` on that branch, no other property. An active run carries
// `skipped: false` plus every payload field `openCaptureRun` actually assigns before returning
// (`run_id`, `opening_digest`, `opening`, `opening_assets`, `entries`) — none of those are
// optional there, because `closeCaptureRun` reads all five unconditionally off a non-skipped
// `runState`, and a caller driving W5 needs `run_id` narrowed to `string` to pass as
// `recordChapterProvenance`'s `expectedRunId`. `closed` alone stays optional: absent before
// `closeCaptureRun` runs, `true` on the runState it returns (`{...runState, closed: true}`), and
// never read back by anything in this module — a caller-facing marker only. Pinned at runtime by
// the "RunState union" tests in capture-record.test.mjs, since nothing in this repository compiles
// TypeScript and a `.d.mts`-only change is otherwise invisible to the whole suite.
export type RunState =
  | { skipped: true }
  | {
      skipped: false;
      run_id: string;
      opening_digest: string;
      opening: BuildIdentity;
      opening_assets: Record<string, Record<string, string>>;
      entries: ChapterEntryLike[];
      closed?: boolean;
    };

export type OpenResult = { ok: true; runState: RunState } | HaltResult | NeedsUiRead;

/** See capture-record.mjs: ledger row 2 — re-assert ownership, establish the hierarchy, snapshot the opening asset hashes, resolve the opening identity, and reserve the one-shot pending token via an exclusive create. */
export function openCaptureRun(
  profileLike: ProfileLike,
  entries: ChapterEntryLike[],
  openingObservation?: UiReadObservation | null,
  deps?: Partial<CaptureRecordDeps>,
): OpenResult;

export interface CaptureOutcome {
  ok: boolean;
  detail?: string;
}

export type CloseResult =
  | { ok: true; runState: RunState; warnings: string[] }
  | HaltResult
  | NeedsUiRead;

/** See capture-record.mjs: ledger row 3 — verify the token, snapshot the closing asset hashes, resolve the run's final identity, commit the run record by temp-then-rename, then remove every leftover matching temp and the token. */
export function closeCaptureRun(
  profileLike: ProfileLike,
  runState: RunState,
  captureOutcome: CaptureOutcome,
  closingObservation?: UiReadObservation | null,
  deps?: Partial<CaptureRecordDeps>,
): CloseResult;

export type RecordResult = { recorded: true; reason: null } | { recorded: false; reason: string } | HaltResult;

/** See capture-record.mjs: ledger row 4 — the completeness rule (rules 1-5); abstains (writes nothing, keeps the prior record) on any failure. */
export function recordChapterProvenance(
  profileLike: ProfileLike,
  acceptedEntries: ChapterEntryLike[],
  entry: ChapterEntryLike,
  chapterFile: string,
  expectedRunId: string,
  deps?: Partial<CaptureRecordDeps> & { expectedAssets?: ExpectedAssetsOverride },
): RecordResult;

// [codex round 5, finding 3] Deliberately NOT one of row 6's repair trio below, despite an
// identical shape to `RepairResult` — this function answers a different question (is there a
// leftover chapter-record temp under `chapters/`?) over a domain row 6's `(token, record, temps)`
// tuple never covers (`run/` only). See capture-record.mjs's own comment above this function's
// definition for why chapter temps are not folded into that tuple. Given its own type rather than
// reusing `RepairResult`, so a reader cannot infer trio membership from the shape alone; `noop` is
// never actually part of this function's contract — there is no `expected`-fingerprint round-trip
// here to make an already-swept call idempotent-and-reported-as-such, so it is simply omitted
// rather than carried over as an always-false field.
export type ChapterTempSweepResult =
  | { ok: true; removed: string[] }
  | { ok: true; skipped: true; removed: [] }
  | HaltResult;

/** See capture-record.mjs: finds and best-effort-removes every leftover `<slug>.json.<uuid>.tmp` chapter-record temp for each of `entries` — the artifact a crashed `recordChapterProvenance` leaves behind between closing its temp and renaming it into place. Entries-driven, like `recordChapterProvenance`/`buildProvenanceReport` above — never a raw directory walk of `chapters/`. Never reads `deps.expectedAssets` (it never extracts or hashes anything), so its `deps` seam is the plain `CaptureRecordDeps`, unlike its two neighbors above. */
export function sweepChapterProvenanceTemps(
  profileLike: ProfileLike,
  entries: ChapterEntryLike[],
  deps?: Partial<CaptureRecordDeps>,
): ChapterTempSweepResult;

// [round 5, codex finding 4] `current_source` is `null` on exactly one branch: `buildProvenanceReport`'s
// ownership-skip row (capture-record.mjs, the `ownership.skip` branch off `assertProvenanceOwnership`)
// — a skipped profile performs zero identity resolutions, so this field is set to `null` there,
// alongside the sibling `source: null` already on this same row. Every OTHER branch always assigns
// `classifyBuildDelta`'s own `current_source` (build-identity.d.mts: `IdentitySource`, one of
// `'command' | 'ui' | 'unavailable'`), never `null` — measured via `classifyBuildDelta`, which reads
// `current.source` unconditionally on every return path and never substitutes a null. Typed as the
// pre-existing looser `string | null` (matching this interface's own `value`/`source` fields) rather
// than tightened to `IdentitySource | null`, to keep this fix scoped to the missing-vs-present defect
// rather than also redesigning the field's string type.
export interface ReportRow {
  key: string;
  value: string;
  source: string | null;
  resolution_reason: string | null;
  classification: 'unchanged' | 'changed' | 'indeterminate';
  classification_reason: string | null;
  current_source: string | null;
}

export type ReportResult = { rows: ReportRow[] } | NeedsUiRead | HaltResult;

/** See capture-record.mjs: ledger row 5 — reads chapter records only (never the run record), verifies against current assets, and classifies the delta in manifest order. Like `recordChapterProvenance`, the runtime reads `deps.expectedAssets` (capture-record.mjs: `deps?.expectedAssets ?? expectedAssets`) with the same six-argument shape — see `ExpectedAssetsOverride`'s own comment for why that seam is a bolt-on intersection rather than a `CaptureRecordDeps` member. [round 5, codex finding 5] Previously omitted from this signature entirely, which let a TypeScript caller pass an `expectedAssets` override here that `recordChapterProvenance`'s own declaration would have rejected — the same seam, differently typed depending only on which function you called. */
export function buildProvenanceReport(
  profileLike: ProfileLike,
  entries: ChapterEntryLike[],
  currentObservation?: UiReadObservation | null,
  deps?: Partial<CaptureRecordDeps> & { expectedAssets?: ExpectedAssetsOverride },
): ReportResult;

export type Row6State =
  | 'not_active'
  | 'orphan_temp'
  | 'absent'
  | 'partial'
  | 'malformed'
  | 'prepared'
  | 'open'
  | 'committed'
  | 'divergent';

export interface ExpectedFingerprint {
  state: Row6State;
  run_id: string | null;
  opening_digest: string | null;
}

// [round 6] `action` is the prescribed repair's EXPORT NAME, and the runtime can only ever produce
// one of two of them or `null` — the private `REPAIR_FOR_STATE` is a closed five-key map onto
// `'abortCaptureRun'`/`'cleanupCommittedRun'`, read through `?? null`,
// so the four states with no prescribed repair (`not_active`, `absent`, `malformed`, `divergent`)
// yield `null`. Measured across all nine states: no third string is reachable. Narrowed from the
// previous bare `string | null` for the same reason `Row6State` directly above is a closed literal
// union rather than `string` — a caller dispatching on this value was having to compare against
// string literals the type never promised, and the looseness was inconsistent within one file.
export type RecoveryVerdict =
  | {
      state: Row6State;
      action: 'abortCaptureRun' | 'cleanupCommittedRun' | null;
      expected: ExpectedFingerprint;
      files: string[];
    }
  | HaltResult;

/**
 * See capture-record.mjs: ledger row 6 — the nine-state classifier over (token, record, temps)
 * observed under `run/` after gate 6. Mutates nothing (measured: the tree is byte-identical before
 * and after, for all nine states).
 *
 * [round 6] TOTAL over its inputs, but eight of the nine states are the ones decided from that
 * tuple: `not_active` is NOT. It is returned from this function's own first branch, off
 * `assertProvenanceOwnership(...).skip` — this run's own W1 ownership outcome — BEFORE any token,
 * record or temp is read, and comes back with `files: []` (measured). The runtime's own docstring
 * says so ("with zero token/record/temp reads"); the previous one-line version of this sentence
 * dropped that clause and so described the tuple as deciding a state it never sees. Chapter-record
 * temps under `chapters/` are outside this tuple by design and are swept separately — see
 * `sweepChapterProvenanceTemps` above.
 */
export function recoverProvenanceState(profileLike: ProfileLike, deps?: Partial<CaptureRecordDeps>): RecoveryVerdict;

export type RepairResult =
  | { ok: true; removed: string[]; noop?: true }
  | { ok: true; skipped: true; removed: [] }
  | HaltResult;

/** See capture-record.mjs: ledger row 6 — repairs `orphan_temp`/`partial`/`prepared`/`open`: every temp first, the token last. Idempotent. */
export function abortCaptureRun(profileLike: ProfileLike, expected: ExpectedFingerprint, deps?: Partial<CaptureRecordDeps>): RepairResult;

/** See capture-record.mjs: ledger row 6 — repairs `committed`: every temp first, the token last, only at the expected fingerprint. Idempotent. */
export function cleanupCommittedRun(profileLike: ProfileLike, expected: ExpectedFingerprint, deps?: Partial<CaptureRecordDeps>): RepairResult;

/** The injectable filesystem seam every exported function accepts as its last argument, defaulting to node:fs/node:crypto/node:child_process bindings. */
export interface CaptureRecordDeps {
  openSync: (...args: unknown[]) => number;
  closeSync: (fd: number) => void;
  readSync: (...args: unknown[]) => number;
  writeSync: (...args: unknown[]) => number;
  fstatSync: (fd: number) => unknown;
  lstatSync: (path: string) => unknown;
  readlinkSync: (path: string) => string;
  realpathSync: (path: string) => string;
  mkdirSync: (path: string) => void;
  unlinkSync: (path: string) => void;
  renameSync: (from: string, to: string) => void;
  readdirSync: (...args: unknown[]) => unknown;
  randomUUID: () => string;
  // [round 6] `CommandOutcome`, not a hand-rolled restatement of it. This member's result is not
  // consumed here — it is handed STRAIGHT to build-identity's `resolveBuildIdentity` as its
  // `commandOutcome`, at all three of this module's identity-resolution points (`openCaptureRun`,
  // `closeCaptureRun`, `buildProvenanceReport`), and that function's own declaration types the field
  // `CommandOutcome`. The previous inline `{ok: boolean; raw?: unknown; detail?: string}` was
  // strictly wider on the success side: it admitted `{ok: true}` with no `raw`, which
  // `CommandOutcome`'s `{ok: true; raw: unknown; ...}` member forbids — so the same seam was typed
  // two different ways depending only on which of the two files a caller happened to read. Measured,
  // the runtime does not crash on the wider shape, it degrades: an injected
  // `runIdentityCommand: () => ({ok: true})` opens the run with
  // `resolution_reason: 'command_output_rejected'` and a null identity. Same defect class as round
  // 5's finding 5 (`expectedAssets`), fixed the same way — one shared alias at the seam.
  runIdentityCommand: (command: string) => CommandOutcome;
}
