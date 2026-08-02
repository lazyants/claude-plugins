// enduser-handbook capture asset — non-normative reference implementation of the build-provenance
// disk layer. The normative contract lives in SKILL.md (W1/W2/W5/W6), whose W2 section carries row
// 6's nine-state recovery table and the repair each state names. [round 11] This header used to
// cite a generated row-6 companion document and the transition source it came from; neither has
// ever existed in
// this repository, on any branch — they were planning artifacts that stayed outside it, so the
// citation sent a downstream reader after a document they could not open. Cite only documents that
// ship; SKILL.md is the authority that does.
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
    // [round 11] NOT `| null`. `assets/profile.schema.json` types this member `"object"` with no null
    // union, so a profile carrying `build_identity: null` is rejected at step 0 with `expected type
    // "object"` — measured — while this declaration used to accept it, letting a TypeScript caller
    // build a ProfileLike production refuses. Absence is the way to say "no provenance", and the
    // runtime's own `!= null` test treats absence exactly as this contract intends. Same shape as the
    // `publish.target` correction above: the schema is what makes the rule real, and a declaration
    // wider than the schema describes a profile that does not exist.
    build_identity?: { command?: string; ui_read?: boolean };
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
// [round 6, codex finding 3] `identityCommandOutcome` carries back the CommandOutcome the aborted
// call already resolved (null when no command is configured). A continuation MUST thread it into the
// retry: without it the retry re-invokes `capture.build_identity.command` — arbitrary operator shell,
// possibly slow, side-effectful, or answering differently the second time — for what is meant to be
// ONE observation point, and silently breaks the twice-per-run contract SKILL.md states.
export type NeedsUiRead = { needs_ui_read: true; region_hint: string; identityCommandOutcome: CommandOutcome | null };

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
  // [round 16] `opening_hazards`/`closing_hazards` are REQUIRED, not optional, and the reader
  // rejects a chapter entry missing either — a record written before they existed reads back as
  // "no hazards", which is the one false statement they exist to prevent. Each member is
  // `<assetDirRelativePath>:<reason>`, where reason is one of `symlink`, `non_regular`, `hard_link`,
  // `inspection_failure` or `vanished` (listed by the directory read, gone before it could be read
  // — uncertainty about something that WAS there, never an absence) — NOT the leaf inspection's
  // `kind`, which is the undiscriminating word
  // `hazard` for all five. W5 splits at the LAST colon so a path containing one is
  // unambiguous. This declaration omitted them for a round while the writer persisted them, so a
  // consumer of `readRunRecordText` could neither inspect nor preserve them.
  // [round 17] That path is NOT necessarily an asset key: a refused DIRECTORY is named by its own
  // path, and the assets it hides are keyed beneath it. Anything reading these lists must match by
  // containment (`key === path || key.startsWith(path + '/')`), never by equality — W5 matched by
  // equality for a round, and a symlinked `screens/` therefore never refused `screens/a.png`.
  // [round 21/22] A directory is named for one further reason: its IDENTITY (`dev`/`ino`) is checked
  // before its listing, between the listing and any use of it, and after its entries are processed.
  // A subdirectory replaced mid-walk is refused when the replacement is STILL IN PLACE at one of
  // those three observations — `<dir>:symlink` when the replacement is a symlink,
  // `<dir>:inspection_failure` when it is a different ordinary directory, which a directory-vs-type
  // check could not see at all. On the third of those paths the map may still hold hashes keyed
  // beneath the refused directory: they are deliberately retained, and read through the containment
  // rule above they are refused; read by EQUALITY they are silently trusted.
  // Stated because a consumer may not assume more than this buys, and the qualification above is
  // the first half of it: each observation re-resolves a path rather than holding the object the
  // previous one saw, so a replacement installed and withdrawn between two adjacent observations
  // leaves no hazard at all. This seam DETECTS a substitution that persists; it prevents none.
  // [round 17/23] The second half: a substitution that lands between a parent's listing and the
  // child's own first observation cannot be detected here at all, since a `Dirent` reports a name
  // and a type and no inode. The asset ROOT is not subject to that particular gap — its identity is
  // supplied by the caller from gate 3's own observation, of the resolved target rather than of a
  // symlink naming it.
  // [round 17] Each member is VALIDATED, not merely typed: a member with no colon, an empty path, a
  // `.`/`..`/empty segment, or a reason outside the five words above makes the whole record
  // `bad_chapter_hazards:<field>`. That is deliberately fail-closed — an unreadable member was
  // silently dropped before, and a dropped hazard is indistinguishable from no hazard, which is the
  // false-provenance path these lists exist to close. A hand-written record must satisfy this.
  chapters: Record<string, {
    opening: Record<string, string>;
    closing: Record<string, string>;
    opening_hazards: string[];
    closing_hazards: string[];
  }>;
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
// (`run_id`, `opening_digest`, `opening`, `opening_assets`, `opening_asset_hazards`, `entries`,
// `output_root`) —
// none of those are optional there, because `closeCaptureRun` reads them unconditionally off a non-skipped
// `runState`, and a caller driving W5 needs `run_id` narrowed to `string` to pass as
// `recordChapterProvenance`'s `expectedRunId`. `closed` alone stays optional: absent before
// `closeCaptureRun` runs, `true` on the runState it returns, and never read back by anything in
// this module — a caller-facing marker only. [round 39] That returned state is RECONSTRUCTED from
// the authenticated payload plus the token-verified `run_id` and recomputed `opening_digest`, never
// spread out of the caller's object: the field types above are a promise to the caller, and a
// caller-held accessor answering differently on a second read broke it while the committed record
// stayed correct — W5, driven off the returned `run_id`, then refused an intact run. Pinned at runtime by
// the "RunState union" tests in capture-record.test.mjs, since nothing in this repository compiles
// TypeScript and a `.d.mts`-only change is otherwise invisible to the whole suite.
// [round 44] The BOUNDARY of what `closeCaptureRun` authenticates, stated once here because it is a
// property of this type rather than of any one guard. Everything the close RECORDS is authenticated:
// the opening payload against the pending token's digest, the run id against that token, and the
// output root against what the open observed — a caller cannot make this module commit a record
// asserting something it did not observe, which is the whole point of the hardening in rounds 37-43.
// What is NOT authenticated is a caller's claim that there is nothing to record. A `Proxy` traps
// `ownKeys`, `getOwnPropertyDescriptor` and `get`, so it can answer every reflective question
// exactly as `{skipped: true}` would while wrapping a genuinely active run; no by-value shape test
// can tell the two apart, because the caller supplies both of this function's inputs. The pending
// token catches the ordinary case and there is a regression test for it — a reservation on disk is
// not something the caller's object answers for — but a caller that ALSO relocates the profile's
// provenance root sends that lookup somewhere else, and the close then returns `{skipped: true}`
// having recorded nothing.
//
// That residual is documented rather than defended, deliberately. It produces no false record: the
// outcome is exactly what calling nothing at all produces — no record written, the reservation left
// on disk, the next `openCaptureRun` halting on `run_already_open`, and `recoverProvenanceState`
// reporting `open` with `abortCaptureRun` as the repair (all measured). Closing it needs an
// unforgeable in-process brand, which would make this type no longer plain data and would refuse a
// legitimate caller that round-trips a skipped state through JSON — trading a no-op a caller can
// already obtain by not calling the function for a false refusal of one that is behaving correctly.
export type RunState =
  | { skipped: true }
  | {
      skipped: false;
      run_id: string;
      opening_digest: string;
      opening: BuildIdentity;
      opening_assets: Record<string, Record<string, string>>;
      // [round 15] Per chapter key, the assets that could NOT be hashed at open, in the same
      // `<relPath>:<reason>` form the persisted `RunRecord` chapters use — see the note on
      // `opening_hazards`/`closing_hazards` above for the reason vocabulary, the fact that the path
      // may name a DIRECTORY, and why matching must be by containment. This comment said `<kind>`
      // for two rounds, which is the exact field the runtime does NOT put there: a consumer
      // following it would present the undiscriminating word `hazard` and lose the detail the
      // hazard exists to carry. Separate from `opening_assets` on purpose: dropping them left an
      // absent key, and W5 reads an absent opening key as "brand-new file this run" and skips the
      // did-it-change check — so a stale asset that merely could not be read at open was recorded
      // as if the captured build had produced it. Authenticated by `opening_digest` along with the
      // rest of the payload, since clearing it would otherwise unblock exactly that record.
      opening_asset_hazards: Record<string, string[]>;
      entries: ChapterEntryLike[];
      // [round 37] What `openCaptureRun` observed of `capture.output_dir`, so `closeCaptureRun` can
      // check the root it walks is the one the run opened over. `identity` is the directory's
      // `<dev>:<ino>`, or `null` when the root did not exist yet — the ordinary first capture, where
      // the capture command is expected to create it. `anchor` is non-null EXACTLY when `identity`
      // is null: the deepest ancestor that DID exist, which is the object that absence was
      // established against and therefore the only thing the close can re-check it against.
      // Authenticated by `opening_digest` like the rest of the payload; a caller that clears it gets
      // `stale_replay`, not a tolerated older shape.
      output_root: {
        canonical: string;
        identity: string | null;
        // [round 40] `tail` is the path BELOW the anchor whose absence was established — the raw
        // segments, re-joined onto the anchor's resolution at close and compared exactly. Without
        // it the close could only ask whether the root ended up somewhere under the anchor, which a
        // previous build's tree elsewhere under that same anchor satisfies.
        anchor: { path: string; identity: string; tail: string[] } | null;
      };
      closed?: boolean;
    };

// [round 9, codex finding 1] The needs_ui_read branch carries `warnings` because the release that
// makes a continuation safe can itself fail. Present and EMPTY on a clean release, so a caller
// cannot read "no warnings" as "this build does not report them"; non-empty names the token that is
// still on disk, which is the difference between a re-triable continuation and one that will halt on
// `run_already_open` for a reason the first result never mentioned. Widened only here rather than on
// `NeedsUiRead` itself, which is shared with two entrypoints that hold no reservation.
export type OpenResult = { ok: true; runState: RunState } | HaltResult | (NeedsUiRead & { warnings: string[] });

/** See capture-record.mjs: ledger row 2 — re-assert ownership, establish the hierarchy, reserve the one-shot pending token via an exclusive create, resolve the opening identity, snapshot the opening asset hashes, and finalize the reserved token with the resolved runState. The reservation comes before this call spends anything of the operator's — the identity command, and any asset hashing — on purpose: a contended open must fail before it spends an operator's identity command, and must not return `needs_ui_read` for a run that could never have started. [round 15] It is NOT first overall, and this sentence used to say it was: the ownership gate, the hierarchy and the entry validation run ahead of it, so an invalid slug halts without a token ever being attempted. That is deliberate — a refusal on grounds unrelated to contention must not leave a reservation behind. A `needs_ui_read` return releases the reservation so the continuation re-checks it cleanly; a release that could not actually remove the token surfaces a non-empty `warnings` array (present and empty on a clean release) rather than silently promising a clean check it did not deliver. Every path that leaves this function after the reservation either finalizes it or releases it — including one taken by a throw. A throw out of IDENTITY RESOLUTION returns `identity_resolution_threw`, named apart from `provenance_hazard` because it reports a malformed observation or a failing identity command rather than a disk condition; a throw out of RUN-STATE CONSTRUCTION is the disk-adjacent one and stays `provenance_hazard`. Neither escapes this declared result. */
export function openCaptureRun(
  profileLike: ProfileLike,
  entries: ChapterEntryLike[],
  openingObservation?: UiReadObservation | null,
  deps?: Partial<CaptureRecordDeps>,
  identityCommandOutcome?: CommandOutcome | null,
): OpenResult;

export interface CaptureOutcome {
  ok: boolean;
  detail?: string;
}

export type CloseResult =
  | { ok: true; runState: RunState; warnings: string[] }
  | HaltResult
  | NeedsUiRead;

/** See capture-record.mjs: ledger row 3 — verify the token, resolve the run's final identity, re-run the entry gates (1-4) over the token-authenticated `runState.entries`, snapshot the closing asset hashes, commit the run record by temp-then-rename, then remove every leftover matching temp and, only once every one is confirmed gone, the token — a temp that cannot be confirmed removed is named in `warnings` and leaves the token in place, so the next `openCaptureRun` halts on it rather than succeeding over residue nothing would otherwise make an operator clean up.  Both identity-resolution calls here can throw — a malformed closing observation, or an identity command that threw — and are converted to an `identity_resolution_threw` halt rather than escaping this declared contract; the closing temp's own `randomUUID()` naming call is guarded the same way, before any temp exists to orphan.  [round 33] Because the entry gates run here too, this call can now return any halt they produce — `physical_asset_dir_collision` and `asset_dir_escapes_output_dir` most notably, neither of which a close could previously report. It ran none of them until this round, so two chapter directories aliasing ONE physical directory closed clean with the identical hash committed under both chapter keys, and no later stage could tell the pair apart. The gates are re-run rather than trusted from the open because the tree moved in between — that is the whole point of a closing observation. */
export function closeCaptureRun(
  profileLike: ProfileLike,
  runState: RunState,
  captureOutcome: CaptureOutcome,
  closingObservation?: UiReadObservation | null,
  deps?: Partial<CaptureRecordDeps>,
  identityCommandOutcome?: CommandOutcome | null,
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
// [round 6, codex finding 2] `removed` lists only what this call CONFIRMED gone, and `warnings` is
// how it says otherwise: `unlinkBestEffort` swallows its failure by design, so before this the sweep
// pushed every candidate onto `removed` whether or not the unlink actually happened, and an operator
// read a false clean. Every other caller of that helper can afford the silence because row 6's
// repair states are their fallback — this one has none, since row 6's `temps` observation is
// `run/`-only by design. `warnings` is present and empty on a clean sweep rather than optional, so a
// caller cannot mistake "no warnings" for "this build of the module does not report them".
export type ChapterTempSweepResult =
  | { ok: true; removed: string[]; warnings: string[] }
  | { ok: true; skipped: true; removed: []; warnings: [] }
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
  // [round 15] Present on EVERY row, `null` when the record is clean. Names why a record is not
  // clean — which asset, and whether its bytes changed, went missing from the record, or could not
  // be read at all. Failing closed is the right verdict and was already correct; collapsing four
  // distinct causes into one `record_stale` row was not, because "the content changed" and "the
  // content could not be read" call for different operator actions. Not optional: a field that
  // appears only sometimes is treated as optional by whatever renders it, and an absent key reads
  // differently from "nothing to report".
  record_detail: string | null;
}

export type ReportResult = { rows: ReportRow[] } | NeedsUiRead | HaltResult;

/** See capture-record.mjs: ledger row 5 — reads chapter records only (never the run record), verifies against current assets, and classifies the delta in manifest order. Like `recordChapterProvenance`, the runtime reads `deps.expectedAssets` (capture-record.mjs: `deps?.expectedAssets ?? expectedAssets`) with the same six-argument shape — see `ExpectedAssetsOverride`'s own comment for why that seam is a bolt-on intersection rather than a `CaptureRecordDeps` member. [round 5, codex finding 5] Previously omitted from this signature entirely, which let a TypeScript caller pass an `expectedAssets` override here that `recordChapterProvenance`'s own declaration would have rejected — the same seam, differently typed depending only on which function you called.  Its current-identity resolution goes through the same guard as `openCaptureRun` and `closeCaptureRun`: a malformed observation or a throwing identity command becomes an `identity_resolution_threw` halt rather than escaping this function's declared, non-throwing contract. W6 is the audit entrypoint an operator runs over already-merged chapters, and the observation reaching it comes from a UI read — untrusted input by references/capture-safety.md. */
export function buildProvenanceReport(
  profileLike: ProfileLike,
  entries: ChapterEntryLike[],
  currentObservation?: UiReadObservation | null,
  deps?: Partial<CaptureRecordDeps> & { expectedAssets?: ExpectedAssetsOverride },
  identityCommandOutcome?: CommandOutcome | null,
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

// [round 6, codex] Six of the eleven members below were typed by ESCAPE HATCH rather than to the
// seam this module actually drives through them: `openSync`/`readSync`/`writeSync` took
// `(...args: unknown[])`, and `fstatSync`/`lstatSync`/`readdirSync` returned bare `unknown`.
// Measured against every real call site in capture-record.mjs (an exhaustive grep of
// `deps.openSync`/`d.openSync`, `deps.readSync`, `deps.writeSync`, `deps.fstatSync`,
// `deps.lstatSync`, `deps.readdirSync`, plus every place their results are read), that escape
// hatch cut both ways: a correct, narrower override like `openSync: (path: string, flags: number)
// => number` was REJECTED — `string`/`number` are narrower than the `unknown` the seam promised to
// pass, so a caller supplying them is contravariantly unsound the OTHER way (codex's own probe,
// against an in-memory compiler host) — while an incorrect one like `lstatSync: () => ({})` was
// ACCEPTED with no diagnostic and crashes at runtime the instant `inspectDirComponent` calls
// `st.isSymbolicLink()`. Every member below is now typed to the minimum this module's own call
// sites actually pass in and actually read off the result — not node:fs's full overload set, which
// would accept far more than this seam needs and give an adopter writing an override no narrower a
// contract than reading node:fs's own types would. This file adds no dependency on node:fs's
// ambient types anywhere (`LstatResultLike`/`FstatResultLike`/`DirentLike` below are local, minimal
// shapes) — a real `fs.Stats`/`fs.Dirent` instance satisfies each structurally, which is what keeps
// `defaultDeps` (capture-record.mjs, real node:fs bindings) a legal `CaptureRecordDeps` under the
// tightened types (verified against these exact declarations via an in-memory `tsc --strict` run
// assigning wide, node:fs-shaped stand-ins — see this round's report, not re-derived here).

/** The subset of `fs.Stats` `lstatSync`'s result is actually read through — `inspectDirComponent` (capture-record.mjs) calls `st.isSymbolicLink()` and `st.isDirectory()`; `direntType` calls those two plus `st.isFile()`; `identityOfListedObject` calls `isSymbolicLink()`/`isDirectory()` and reads `dev`/`ino`. [round 23] `validateEntriesForCapture`'s existence check is no longer among the sites that discard their result — it now feeds `identityOfListedObject`, and the sentence claiming otherwise survived a round after that stopped being true. The two remaining sites that genuinely never read the result are gate 3's containment probe and gate 5's `canonicalizeForComparison`. [round 19] `isFile` was added to the runtime one round before it was added here, and a mock conforming exactly to the two-predicate version crashed the opening snapshot with `st.isFile is not a function`. `direntType` calls it optionally and treats a result that cannot answer as a hazard, so a caller still on the old contract degrades rather than throwing — but this declaration is what a new caller writes to, and it says what the runtime needs. */
export interface LstatResultLike {
  isSymbolicLink(): boolean;
  isDirectory(): boolean;
  isFile(): boolean;
  // [round 22] The asset walk compares directory IDENTITY across its own listing, not merely
  // directory-ness: two `lstat`s that both answer "directory" say nothing about it being the SAME
  // directory, so a subdirectory replaced by a DIFFERENT ordinary directory passed a type-only
  // check with a foreign hash and no hazard (codex produced that as executed evidence). `dev`/`ino`
  // are the identity, and gate 3's observation of an asset root is carried forward as the same
  // pair. A result that cannot answer with two numbers is a hazard, never a guess — so an
  // implementation omitting them degrades to a refusal rather than to a silent pass.
  // [round 23] Two things this pair does NOT mean, both of which shipped as round-22 comments.
  // (1) For a SYMLINK it is the link's identity, and `readdirSync` follows the link — so the walk
  // reads the identity of what the link RESOLVES to (`realpathSync`, then `lstatSync` of the
  // result), never the link's own. A link is a stable name for a changing object: pinning it
  // pinned nothing, and a swapped target passed all three checks with a foreign digest and no
  // hazard. (2) "Degrades to a refusal" is the whole run for an asset ROOT: gate 3 halts rather
  // than carrying a null pin forward, because the snapshot could not tell a null pin apart from a
  // caller that configured none. `rootMustExist` never covered replacement — only disappearance.
  // [round 24] `number | bigint`, and the widening is the point rather than a convenience: these are
  // 64-bit values and a JavaScript number cannot hold every one of them. Inodes `9007199254740992`
  // and `9007199254740993` — two different directories — both render the id `7:9007199254740992`,
  // so on any filesystem exposing identifiers above 2^53 a substitution passes every observation
  // point. A `number` is therefore accepted only when `Number.isSafeInteger` says it is EXACT;
  // anything larger is a hazard, not a comparison. A `bigint` is exact by construction, which is
  // what the `{ bigint: true }` request on `lstatSync` above exists to obtain. Both spellings render
  // the same digits for the same object, so a seam mixing them still compares equal.
  dev: number | bigint;
  ino: number | bigint;
}

/** The subset of `fs.Stats` `fstatSync`'s result is actually read through — `openLeafNoFollow` (capture-record.mjs, gate 6) calls only `stat.isFile()` and reads `stat.nlink`. The sole call site. */
export interface FstatResultLike {
  isFile(): boolean;
  nlink: number;
}

/** The subset of `fs.Dirent` a `readdirSync(path, {withFileTypes: true})` result is actually read through — `direntType` (capture-record.mjs, for `walkRegularFiles`, the sole call site of this overload) reads `dirent.name` and calls `isSymbolicLink()`, `isDirectory()`, `isFile()`, and then, only when all three answer false, `isSocket()`, `isFIFO()`, `isCharacterDevice()` and `isBlockDevice()`. [round 19] That last group is OPTIONAL and declared so: all-three-false is `UV_DIRENT_UNKNOWN` on filesystems that do not fill in `d_type`, and the runtime resolves that case with an `lstat` rather than by guessing — so an implementation providing only the first three still behaves correctly, at the cost of one extra `lstat` per entry. This comment used to say the runtime reads nothing beyond the first three, which stopped being true a round before it was corrected. */
export interface DirentLike {
  name: string;
  isSymbolicLink(): boolean;
  isDirectory(): boolean;
  isFile(): boolean;
  isSocket?(): boolean;
  isFIFO?(): boolean;
  isCharacterDevice?(): boolean;
  isBlockDevice?(): boolean;
}

/**
 * The injectable filesystem seam every exported function THAT TOUCHES DISK accepts, defaulting to
 * node:fs/node:crypto/node:child_process bindings. The pure exports take no `deps` — [round 14]
 * "every exported function" was false here too, written while correcting the neighbouring false
 * universal about argument position. The exact set is pinned by a test, not by this sentence.
 *
 * [round 13] This said "as its last argument". It is not last in `openCaptureRun`,
 * `closeCaptureRun` or `buildProvenanceReport`, each of which takes `identityCommandOutcome` after
 * it — the same wrong-slot trap round 12 removed from SKILL.md, restated here as a rule. The
 * declarations below carry the real positions; this text no longer claims one.
 */
export interface CaptureRecordDeps {
  // [round 6] `(path, flags)` at `openLeafNoFollow` (gate 6's read-only opens: `flags | fs.constants.O_NOFOLLOW`)
  // and `(path, flags, mode)` at `openCaptureRun`'s pending-token create
  // (`O_CREAT|O_EXCL|O_WRONLY|O_NOFOLLOW`, `0o644`) are the only two call sites — `mode` is optional
  // here for that reason (the second call site always supplies it), not because node:fs's own
  // signature happens to make it optional.
  openSync: (path: string, flags: number, mode?: number) => number;
  closeSync: (fd: number) => void;
  // [round 6] The sole call site, `readAllFromFd`, always passes all five positional arguments;
  // `position` is `null` on every call in this module (never a numeric offset), but the type stays
  // `number | null` rather than narrowing to the literal `null` this module happens to always
  // pass — the seam describes what node:fs's `readSync` accepts here, not merely what this module
  // currently supplies. `buffer` is `Uint8Array` rather than a bare `Buffer` so this file adds no
  // dependency on node:fs's ambient `Buffer` global — every `Buffer` this module actually passes
  // (`Buffer.alloc(...)`) is structurally a `Uint8Array`.
  readSync: (fd: number, buffer: Uint8Array, offset: number, length: number, position: number | null) => number;
  // [round 6] The sole call site, `writeFull`, always passes exactly these two arguments — no
  // offset/length/position. See `readSync` above for why `buffer` is `Uint8Array`, not `Buffer`.
  writeSync: (fd: number, buffer: Uint8Array) => number;
  fstatSync: (fd: number) => FstatResultLike;
  // [round 24] The optional second argument is a REQUEST for exact identity values, passed at the
  // three call sites that read `dev`/`ino` and nowhere else. `node:fs` honours it and answers with
  // `BigIntStats`. An implementation that ignores it is still conforming — it simply answers in
  // numbers, which stay exact inside the safe-integer window and are REFUSED beyond it rather than
  // rounded into a false match. So the parameter widens what a caller may do, never what it must.
  lstatSync: (path: string, options?: { bigint: true }) => LstatResultLike;
  readlinkSync: (path: string) => string;
  realpathSync: (path: string) => string;
  // [round 6, follow-up] `ensureDirComponent` calls the bare `(path)` form (gate 6's per-component
  // discipline — one directory at a time, never recursive); `establishHierarchy` calls
  // `(path, {recursive: true})` for `publish.chapters_dir`, which may need to come into existence
  // several levels deep on a brand-new handbook's first capture. A real call site the previous
  // `(path: string) => void` type rejected — the same defect shape as the six members above, found
  // while auditing the rest of this interface rather than named in codex's own finding. Neither call
  // site reads the return value (both are wrapped in a try/catch that only branches on `err.code`),
  // so `void` stays correct even though node:fs's own `mkdirSync` returns `string | undefined` when
  // `recursive` is set — a `void`-typed seam member accepts any real return value, it just never
  // promises one back to a caller.
  mkdirSync: (path: string, options?: { recursive: true }) => void;
  unlinkSync: (path: string) => void;
  renameSync: (from: string, to: string) => void;
  // [round 6] Overloaded like node:fs's own `readdirSync`, but only the two shapes this module
  // actually calls: `listMatchingTempsIn`'s bare `readdirSync(dir)` (a filename listing, filtered
  // and mapped as plain strings) and `walkRegularFiles`'s `readdirSync(absDir, {withFileTypes:
  // true})` (a `Dirent`-like listing — the only place this module distinguishes file/directory/
  // symlink kind without a separate stat call). Previously `(...args: unknown[]) => unknown`, which
  // admitted a non-array override (e.g. returning a bare number) with no diagnostic; the module's
  // own two call sites already assume the result is iterable (`for...of`, `.filter`/`.map`), an
  // assumption this signature now states rather than leaves implicit.
  readdirSync: {
    (path: string): string[];
    (path: string, options: { withFileTypes: true }): DirentLike[];
  };
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
