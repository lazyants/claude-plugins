// enduser-handbook capture asset — non-normative reference implementation of the build-provenance
// disk layer. The normative contract lives in SKILL.md (W1/W2/W5/W6) and row6-generated.md (row
// 6's state table, signature rows, ledger row and test matrix, generated from ROW6-TRANSITIONS).
//
// capture-record.d.mts — TypeScript declarations for capture-record.mjs so a downstream
// typechecking project resolves the .ts -> .mjs import. This repo does not compile TypeScript.

import type { BuildIdentity, UiReadObservation } from './build-identity.d.mts';

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
    target?: string;
  };
}

export interface Halt {
  halt: string;
  message?: string;
  [key: string]: unknown;
}

export type HaltResult = { ok: false; halts: Halt[] };
export type NeedsUiRead = { needs_ui_read: true; region_hint: string };

/** See capture-record.mjs: RFC 8785 (JCS) canonicalization of an in-memory JS value. */
export function jcsCanonicalize(value: unknown): { ok: true; canonical: string } | { ok: false; reason: string };

/** See capture-record.mjs: SHA-256 of the UTF-8 bytes of an already-canonicalized string, hex-encoded. */
export function sha256HexOfCanonical(canonical: string): string;

/** See capture-record.mjs: the `sha256:`-prefixed digest of an opening payload's canonical form. Throws on an uncanonicalizable payload. */
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
  deps?: Partial<CaptureRecordDeps> & { expectedAssets?: (...args: unknown[]) => unknown },
): RecordResult;

export interface ReportRow {
  key: string;
  value: string;
  source: string | null;
  resolution_reason: string | null;
  classification: 'unchanged' | 'changed' | 'indeterminate';
  classification_reason: string | null;
  current_source: string;
}

export type ReportResult = { rows: ReportRow[] } | NeedsUiRead | HaltResult;

/** See capture-record.mjs: ledger row 5 — reads chapter records only (never the run record), verifies against current assets, and classifies the delta in manifest order. */
export function buildProvenanceReport(
  profileLike: ProfileLike,
  entries: ChapterEntryLike[],
  currentObservation?: UiReadObservation | null,
  deps?: Partial<CaptureRecordDeps>,
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

export type RecoveryVerdict =
  | { state: Row6State; action: string | null; expected: ExpectedFingerprint; files: string[] }
  | HaltResult;

/** See capture-record.mjs: ledger row 6 — the nine-state TOTAL classifier over (token, record, temps), observed after gate 6. Mutates nothing. */
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
  runIdentityCommand: (command: string) => { ok: boolean; raw?: unknown; detail?: string };
}
