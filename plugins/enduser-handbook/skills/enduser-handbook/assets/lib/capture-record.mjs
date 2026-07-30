// enduser-handbook asset — non-normative reference implementation of the build-provenance disk
// layer. The normative contract lives in SKILL.md (W1's ownership gate, W2's open/close sequence,
// W5's completeness rule + chapter record, W6's report) and in references/capture-engines.md,
// references/capture-safety.md and references/revalidation.md. The state-machine surfaces cited
// throughout this file (the row-6 state table, its signature rows, its ledger row and its test
// matrix) are GENERATED from row6-generated.md's authority and are not restated here in prose.
//
// capture-record.mjs — the ONLY module in this feature that touches disk. Every filesystem
// operation is reached through one injectable seam (the `deps` parameter of every exported
// function), defaulting to `node:fs`/`node:crypto`, so every atomicity and ownership claim below is
// testable by interposing on that seam rather than by inspecting file contents after the fact. The
// pure, no-I/O half of this feature — identity-value normalization, the shared `build_identity`
// validity check, delta classification and report rendering — lives in the sibling module
// `assets/lib/build-identity.mjs`, imported below rather than re-implemented. The embedded-image
// candidate/extraction contract (`buildEmbedCandidates`, `isCanonicalAssetKey`, `expectedAssets`)
// lives in `assets/lib/chapter-paths.mjs`, likewise imported rather than re-implemented.
//
// This module exports the eight named entrypoints of ledger rows 1-6 —
// `assertProvenanceOwnership`, `openCaptureRun`, `closeCaptureRun`, `recordChapterProvenance`,
// `buildProvenanceReport`, and row 6's operator-invoked recovery trio `recoverProvenanceState`,
// `abortCaptureRun`, `cleanupCommittedRun` — plus the two path derivations every stage shares,
// `provenanceRoot` and `chapterRecordPath`. Ten named exports; the eight entrypoints are a
// different count of a different thing (five pipeline stages plus row 6's recovery trio), and the
// plan is explicit that conflating the two counts is itself a defect worth guarding against.
//
// The module performs NO chapter write of its own: its only write targets are the two record kinds
// under the provenance root (`<root>/run/current.json`, `<root>/run/pending.json`,
// `<root>/chapters/<group>/<slug>.json`) and their process-unique temps. W3/W5 authoring and
// publishing the chapter itself are the shipped workflow's own job and are untouched here.

import * as fs from 'node:fs';
import { execSync } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';

import {
  normalizeBuildIdentity,
  sanitizeDetail,
  isValidBuildIdentityField,
  verifyRecord,
  classifyBuildDelta,
  resolveBuildIdentity,
  resolveClosingIdentity,
  formatIdentityValue,
} from './build-identity.mjs';

import {
  chapterAssetDir,
  chapterRelPath,
  isCanonicalAssetKey,
  expectedAssets,
  isValidSlugSyntax,
  findCanonicalPathCollisions,
  resolvePhysicalContainment,
  findPhysicalPathCollisions,
} from './chapter-paths.mjs';

// ---------------------------------------------------------------------------------------------
// Path algebra — private, POSIX-only by construction (see chapter-paths.mjs's identical rationale:
// segments split on '/' AND '\\' so a stray backslash from a Windows-authored profile value still
// normalizes; '.' segments are dropped; every join re-derives from segments rather than
// string-concatenating). This module does NOT import chapter-paths.mjs's private helpers — they
// are not exported, and re-deriving the same small algebra here keeps this module's only
// dependency on that sibling limited to its genuinely shared, exported surface.
// ---------------------------------------------------------------------------------------------

function rawSegments(p) {
  return String(p)
    .split(/[\\/]+/)
    .filter((seg) => seg !== '' && seg !== '.');
}

function isAbsolutePath(p) {
  return /^[\\/]/.test(String(p));
}

function normalizeSegments(segments, absolute) {
  const out = [];
  for (const seg of segments) {
    if (seg !== '..') {
      out.push(seg);
      continue;
    }
    if (out.length > 0 && out[out.length - 1] !== '..') {
      out.pop();
    } else if (!absolute) {
      out.push('..');
    }
  }
  return out;
}

function posixJoin(...parts) {
  const absolute = parts.length > 0 && isAbsolutePath(parts[0]);
  const segments = normalizeSegments(
    parts.flatMap((p) => rawSegments(p)),
    absolute,
  );
  return absolute ? `/${segments.join('/')}` : segments.join('/');
}

// True iff `a`'s segments are a component-wise prefix of `b`'s (including the equal case). Used
// for gate 5's disjointness check: a plain string-prefix compare would wrongly reject the sibling
// pair `vault/handbook-old` vs `vault/handbook` (a real fixture in the plan), since the differing
// FINAL segment is a false positive for a naive `startsWith`.
function isSegmentPrefixOf(a, b) {
  if (a.length > b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

// ---------------------------------------------------------------------------------------------
// The filesystem seam. Every exported function takes `deps` last and defaults to this object; no
// other place in this module references `node:fs`/`node:crypto`/`node:child_process` directly —
// that is the property the capability-policy test (tests/capture-record.test.mjs) checks by
// scanning this file's own source text, not merely by asserting behaviour.
// ---------------------------------------------------------------------------------------------

const defaultDeps = Object.freeze({
  openSync: fs.openSync,
  closeSync: fs.closeSync,
  readSync: fs.readSync,
  writeSync: fs.writeSync,
  fstatSync: fs.fstatSync,
  lstatSync: fs.lstatSync,
  readlinkSync: fs.readlinkSync,
  realpathSync: fs.realpathSync,
  mkdirSync: fs.mkdirSync,
  unlinkSync: fs.unlinkSync,
  renameSync: fs.renameSync,
  readdirSync: fs.readdirSync,
  randomUUID,
  // The `capture.build_identity.command` executor. `command` is a profile-authored shell command
  // string (e.g. "npm pkg get version") at the SAME trust level as `capture.command`, which
  // SKILL.md already runs "exactly as written" through a shell — this is not untrusted external
  // input, so `execSync`'s shell interpretation is the intended behaviour here, not an oversight.
  // No universal safe default exists for running an arbitrary profile-supplied shell command, so
  // production callers may override this; the shipped default reports a structured outcome rather
  // than throwing, so a failing command becomes `command_failed` (build-identity.mjs) rather than
  // an uncaught throw.
  runIdentityCommand(command) {
    try {
      const raw = execSync(command, { encoding: 'utf8' });
      return { ok: true, raw, detail: command };
    } catch (err) {
      return { ok: false, detail: `${command}: ${err.message ?? String(err)}` };
    }
  },
});

function mergeDeps(deps) {
  return deps ? { ...defaultDeps, ...deps } : defaultDeps;
}

// ---------------------------------------------------------------------------------------------
// RFC 8785 (JCS) canonicalization — in-tree, no dependency (this repository ships no
// package.json/lockfile of any kind; see the plan's rationale). Operates on an already-in-memory
// JS value (never on raw, possibly-hand-edited JSON text — that boundary belongs to the record
// readers below, which reject a duplicate key or a lone surrogate in the SOURCE TEXT before
// `JSON.parse` ever runs, since `JSON.parse` silently keeps the last of a duplicate key and does
// not validate surrogate pairing either).
// ---------------------------------------------------------------------------------------------

class CanonicalizeError extends Error {
  constructor(reason) {
    super(`jcs canonicalize: ${reason}`);
    this.reason = reason;
  }
}

// True iff `str` contains an unpaired UTF-16 surrogate code unit — RFC 8785 §3.2.2.2 requires
// rejecting these. A manual code-unit scan, deliberately NOT `String.prototype.isWellFormed()`:
// that landed in Node 20 and this repository declares no Node floor anywhere.
function hasLoneSurrogate(str) {
  for (let i = 0; i < str.length; i++) {
    const code = str.charCodeAt(i);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = str.charCodeAt(i + 1);
      if (Number.isNaN(next) || next < 0xdc00 || next > 0xdfff) return true;
      i++; // consume the low half of a valid pair
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      return true; // low surrogate with no preceding high surrogate
    }
  }
  return false;
}

function canonicalizeJcsString(str) {
  if (hasLoneSurrogate(str)) throw new CanonicalizeError('lone_surrogate');
  let out = '"';
  // Iterate by CODE POINT (recombines a valid surrogate pair into one step); safe here because
  // every lone surrogate was already rejected above, so every surrogate remaining is paired.
  for (const ch of str) {
    if (ch === '"') out += '\\"';
    else if (ch === '\\') out += '\\\\';
    else if (ch === '\b') out += '\\b';
    else if (ch === '\t') out += '\\t';
    else if (ch === '\n') out += '\\n';
    else if (ch === '\f') out += '\\f';
    else if (ch === '\r') out += '\\r';
    else {
      const code = ch.codePointAt(0);
      if (code < 0x20) out += `\\u${code.toString(16).padStart(4, '0')}`;
      else out += ch; // includes every non-ASCII codepoint, emitted RAW (never escaped)
    }
  }
  return `${out}"`;
}

function canonicalizeJcsNumber(num) {
  if (!Number.isFinite(num)) throw new CanonicalizeError('non_finite_number');
  // ECMAScript's shortest round-tripping form is exactly `String(number)` — V8's Number-to-string
  // conversion already implements it, and `(-0).toString() === '0'`, matching JCS's requirement
  // that -0 canonicalizes to "0" with no special-casing needed.
  return String(num);
}

function canonicalizeJcsValue(value) {
  if (value === null) return 'null';
  const t = typeof value;
  if (t === 'undefined') throw new CanonicalizeError('undefined_unsupported');
  if (t === 'function' || t === 'symbol') throw new CanonicalizeError('unsupported_value_type');
  if (t === 'bigint') throw new CanonicalizeError('bigint_unsupported');
  if (t === 'boolean') return value ? 'true' : 'false';
  if (t === 'number') return canonicalizeJcsNumber(value);
  if (t === 'string') return canonicalizeJcsString(value);
  if (Array.isArray(value)) {
    return `[${value.map(canonicalizeJcsValue).join(',')}]`;
  }
  if (t === 'object') {
    // Object.keys returns only the object's OWN enumerable string keys — never inherited members,
    // so a payload cannot smuggle a prototype-chain property into the canonical form. Sorted by
    // plain `Array.prototype.sort()`, i.e. by UTF-16 CODE UNIT — not a locale or code-point
    // comparator, per RFC 8785.
    const keys = Object.keys(value).sort();
    const entries = keys.map((k) => `${canonicalizeJcsString(k)}:${canonicalizeJcsValue(value[k])}`);
    return `{${entries.join(',')}}`;
  }
  throw new CanonicalizeError('unsupported_value_type');
}

/**
 * Canonicalize an in-memory JS value per RFC 8785 (JCS). Rejects rather than coerces: `undefined`,
 * non-finite numbers, functions, symbols, `BigInt`, and a lone (unpaired) surrogate anywhere in a
 * string, at any depth including inside an array or a nested object.
 *
 * @param {unknown} value
 * @returns {{ok: true, canonical: string}|{ok: false, reason: string}}
 */
export function jcsCanonicalize(value) {
  try {
    return { ok: true, canonical: canonicalizeJcsValue(value) };
  } catch (err) {
    if (err instanceof CanonicalizeError) return { ok: false, reason: err.reason };
    throw err;
  }
}

/** SHA-256 of the UTF-8 bytes of an already-canonicalized string, hex-encoded. @param {string} canonical @returns {string} */
export function sha256HexOfCanonical(canonical) {
  return createHash('sha256').update(Buffer.from(canonical, 'utf8')).digest('hex');
}

/**
 * The opening-payload digest stored in the pending token and, a second time, in the run record's
 * `opening_digest` — `sha256:` followed by 64 lowercase hex digits. Throws on an uncanonicalizable
 * payload; callers construct `runState.opening` themselves (or receive it via a real
 * `JSON.parse(JSON.stringify(...))` round trip), so this is a programming-error boundary, not a
 * user-facing halt.
 *
 * @param {unknown} openingPayload
 * @returns {string}
 */
export function digestOpeningPayload(openingPayload) {
  const result = jcsCanonicalize(openingPayload);
  if (!result.ok) {
    throw new Error(`digestOpeningPayload: cannot canonicalize opening payload (${result.reason})`);
  }
  return `sha256:${sha256HexOfCanonical(result.canonical)}`;
}

// `sha256:` followed by exactly 64 lowercase hex digits — the one grammar every stored digest and
// every stored content hash shares (`opening_digest`, and every `opening`/`closing`/`asset_hashes`
// value).
const HASH_GRAMMAR = /^sha256:[0-9a-f]{64}$/;

function isValidDigest(s) {
  return typeof s === 'string' && HASH_GRAMMAR.test(s);
}

// ---------------------------------------------------------------------------------------------
// A hand-rolled, duplicate-key- and lone-surrogate-aware JSON reader — the one boundary where a
// hand-edited or non-JS-produced payload enters. `JSON.parse` silently keeps the last of a
// duplicate key and does not validate surrogate pairing, so neither hazard is visible after
// parsing; this scans the RAW TEXT first. Duplicate keys are compared as DECODED names (an escape-
// equivalent pair like `"a"`/`"a"` is a duplicate even though the two lexemes differ), scoped
// to one JSON object at a time (two different objects may reuse a key freely). This is real JSON
// grammar — unlike JS source, it has no regex/division/template ambiguity — so a small recursive-
// descent reader is a bounded, exact parser, not a heuristic scanner.
// ---------------------------------------------------------------------------------------------

class JsonHazard extends Error {
  constructor(reason) {
    super(`strict json parse: ${reason}`);
    this.reason = reason;
  }
}

function parseJsonStrict(text) {
  let i = 0;
  const n = text.length;

  function skipWs() {
    while (i < n && (text[i] === ' ' || text[i] === '\t' || text[i] === '\n' || text[i] === '\r')) i++;
  }

  function fail(reason) {
    throw new JsonHazard(reason);
  }

  function parseString() {
    if (text[i] !== '"') fail('syntax_error');
    i++;
    let out = '';
    while (true) {
      if (i >= n) fail('syntax_error');
      const ch = text[i];
      if (ch === '"') {
        i++;
        break;
      }
      if (ch === '\\') {
        i++;
        const esc = text[i];
        if (esc === undefined) fail('syntax_error');
        if (esc === '"' || esc === '\\' || esc === '/') out += esc;
        else if (esc === 'b') out += '\b';
        else if (esc === 'f') out += '\f';
        else if (esc === 'n') out += '\n';
        else if (esc === 'r') out += '\r';
        else if (esc === 't') out += '\t';
        else if (esc === 'u') {
          const hex = text.slice(i + 1, i + 5);
          if (!/^[0-9a-fA-F]{4}$/.test(hex)) fail('syntax_error');
          out += String.fromCharCode(Number.parseInt(hex, 16));
          i += 4;
        } else fail('syntax_error');
        i++;
        continue;
      }
      // A raw, unescaped control character (< 0x20) is illegal in JSON text.
      if (ch.charCodeAt(0) < 0x20) fail('syntax_error');
      out += ch;
      i++;
    }
    if (hasLoneSurrogate(out)) fail('lone_surrogate');
    return out;
  }

  function parseLiteral(literal, value) {
    if (text.slice(i, i + literal.length) !== literal) fail('syntax_error');
    i += literal.length;
    return value;
  }

  function parseNumber() {
    const start = i;
    if (text[i] === '-') i++;
    if (text[i] === '0') {
      i++;
    } else if (text[i] >= '1' && text[i] <= '9') {
      i++;
      while (text[i] >= '0' && text[i] <= '9') i++;
    } else {
      fail('syntax_error');
    }
    if (text[i] === '.') {
      i++;
      if (!(text[i] >= '0' && text[i] <= '9')) fail('syntax_error');
      while (text[i] >= '0' && text[i] <= '9') i++;
    }
    if (text[i] === 'e' || text[i] === 'E') {
      i++;
      if (text[i] === '+' || text[i] === '-') i++;
      if (!(text[i] >= '0' && text[i] <= '9')) fail('syntax_error');
      while (text[i] >= '0' && text[i] <= '9') i++;
    }
    return Number(text.slice(start, i));
  }

  function parseValue() {
    skipWs();
    const ch = text[i];
    if (ch === '"') return parseString();
    if (ch === '{') return parseObject();
    if (ch === '[') return parseArray();
    if (ch === 't') return parseLiteral('true', true);
    if (ch === 'f') return parseLiteral('false', false);
    if (ch === 'n') return parseLiteral('null', null);
    if (ch === '-' || (ch >= '0' && ch <= '9')) return parseNumber();
    fail('syntax_error');
    return undefined;
  }

  function parseObject() {
    i++; // consume '{'
    const obj = Object.create(null);
    const seenKeys = new Set();
    skipWs();
    if (text[i] === '}') {
      i++;
      return obj;
    }
    while (true) {
      skipWs();
      const key = parseString();
      if (seenKeys.has(key)) fail('duplicate_key');
      seenKeys.add(key);
      skipWs();
      if (text[i] !== ':') fail('syntax_error');
      i++;
      const value = parseValue();
      obj[key] = value;
      skipWs();
      if (text[i] === ',') {
        i++;
        continue;
      }
      if (text[i] === '}') {
        i++;
        break;
      }
      fail('syntax_error');
    }
    return obj;
  }

  function parseArray() {
    i++; // consume '['
    const arr = [];
    skipWs();
    if (text[i] === ']') {
      i++;
      return arr;
    }
    while (true) {
      arr.push(parseValue());
      skipWs();
      if (text[i] === ',') {
        i++;
        continue;
      }
      if (text[i] === ']') {
        i++;
        break;
      }
      fail('syntax_error');
    }
    return arr;
  }

  try {
    const value = parseValue();
    skipWs();
    if (i !== n) return { ok: false, reason: 'syntax_error' };
    return { ok: true, value };
  } catch (err) {
    if (err instanceof JsonHazard) return { ok: false, reason: err.reason };
    throw err;
  }
}

// ---------------------------------------------------------------------------------------------
// Gate 6 — hazard inspection. Every leaf path (token, chapter record, temp) is opened with
// O_NOFOLLOW, `fstat`'d on that SAME descriptor (never re-opened by path), required to be a
// regular file with `nlink === 1`, and read from that descriptor only. Every hierarchy component
// (the provenance root, its `run/`/`chapters/` namespace directories, a chapter's group directory)
// is `lstat`'d and required to be a real directory reached with no symlink component. An
// `lstat`/`open`/`readlink` failure that is NOT the expected first-run `ENOENT` on a path
// establishment is about to create is its own hazard kind, `inspection_failure` — never silently
// read as "absent" (that reading is the exact mutant that turns this gate off everywhere at once).
// ---------------------------------------------------------------------------------------------

/** @typedef {{kind: 'absent'}|{kind: 'hazard', reason: string, path: string}|{kind: 'directory'}} DirComponentInspection */

function inspectDirComponent(absPath, deps) {
  let st;
  try {
    st = deps.lstatSync(absPath);
  } catch (err) {
    if (err.code === 'ENOENT') return { kind: 'absent' };
    return { kind: 'hazard', reason: 'inspection_failure', path: absPath };
  }
  if (st.isSymbolicLink()) return { kind: 'hazard', reason: 'symlink', path: absPath };
  if (!st.isDirectory()) return { kind: 'hazard', reason: 'non_directory', path: absPath };
  return { kind: 'directory' };
}

// Establish one directory component: create it if absent, then re-inspect and require a real
// directory either way. `mkdir` is called individually per component (never a recursive `mkdir`
// that reports nothing about what it made), and an `EEXIST` race is treated the same as an
// already-inspected absence — re-inspect and apply the identical requirement.
function ensureDirComponent(absPath, deps) {
  const before = inspectDirComponent(absPath, deps);
  if (before.kind === 'directory') return { ok: true };
  if (before.kind === 'hazard') return { ok: false, hazard: before };
  try {
    deps.mkdirSync(absPath);
  } catch (err) {
    if (err.code !== 'EEXIST') {
      return { ok: false, hazard: { kind: 'hazard', reason: 'inspection_failure', path: absPath } };
    }
  }
  const after = inspectDirComponent(absPath, deps);
  if (after.kind !== 'directory') {
    return {
      ok: false,
      hazard:
        after.kind === 'hazard' ? after : { kind: 'hazard', reason: 'inspection_failure', path: absPath },
    };
  }
  return { ok: true };
}

/** @typedef {{kind: 'absent'}|{kind: 'hazard', reason: string, path: string}|{kind: 'present', fd: number, stat: import('node:fs').Stats}} LeafInspection */

// Open a leaf path (token / record / temp) with O_NOFOLLOW and verify it on the SAME descriptor.
// Returns an OPEN fd on success — the caller reads from it and MUST close it. `flags` follows
// node:fs conventions (e.g. fs.constants.O_RDONLY); O_NOFOLLOW is always added by this helper.
function openLeafNoFollow(absPath, flags, deps) {
  let fd;
  try {
    fd = deps.openSync(absPath, flags | fs.constants.O_NOFOLLOW);
  } catch (err) {
    if (err.code === 'ENOENT') return { kind: 'absent' };
    if (err.code === 'ELOOP') return { kind: 'hazard', reason: 'symlink', path: absPath };
    return { kind: 'hazard', reason: 'inspection_failure', path: absPath };
  }
  let stat;
  try {
    stat = deps.fstatSync(fd);
  } catch {
    closeBestEffort(fd, deps);
    return { kind: 'hazard', reason: 'inspection_failure', path: absPath };
  }
  if (!stat.isFile()) {
    closeBestEffort(fd, deps);
    return { kind: 'hazard', reason: 'non_regular', path: absPath };
  }
  if (stat.nlink !== 1) {
    closeBestEffort(fd, deps);
    return { kind: 'hazard', reason: 'hard_link', path: absPath };
  }
  return { kind: 'present', fd, stat };
}

function readAllFromFd(fd, deps) {
  const chunks = [];
  const buf = Buffer.alloc(65536);
  let bytesRead;
  // eslint-disable-next-line no-cond-assign
  while ((bytesRead = deps.readSync(fd, buf, 0, buf.length, null)) > 0) {
    chunks.push(Buffer.from(buf.subarray(0, bytesRead)));
  }
  return Buffer.concat(chunks);
}

// Read a leaf file's full bytes through gate 6 (no-follow, regular, nlink===1, same-descriptor
// read) — the one open/read/close body every leaf reader shares, differing only in what they do
// with the resulting bytes. Returns {kind:'absent'} | {kind:'hazard', reason, path} |
// {kind:'present', bytes}. `readAllFromFd`'s `readSync` was previously unguarded — a mid-read EIO
// escaped this function uncaught (codex round 3), even though every OTHER hazard in this module is
// a returned result, never a throw. Every caller along the chain (readRunRecordFromDisk,
// readChapterRecordFromDisk, and their own callers) already dispatches on `.kind === 'hazard'`, so
// converting a read failure here into that SAME kind needs no change anywhere downstream.
function readLeafBytes(absPath, deps) {
  const opened = openLeafNoFollow(absPath, fs.constants.O_RDONLY, deps);
  if (opened.kind !== 'present') return opened;
  let bytes;
  try {
    bytes = readAllFromFd(opened.fd, deps);
  } catch {
    closeBestEffort(opened.fd, deps);
    return { kind: 'hazard', reason: 'inspection_failure', path: absPath };
  }
  closeBestEffort(opened.fd, deps);
  return { kind: 'present', bytes };
}

function readLeafText(absPath, deps) {
  const read = readLeafBytes(absPath, deps);
  if (read.kind !== 'present') return read;
  return { kind: 'present', text: read.bytes.toString('utf8') };
}

function hashFileNoFollow(absPath, deps) {
  const read = readLeafBytes(absPath, deps);
  if (read.kind !== 'present') return read;
  return { kind: 'present', digest: `sha256:${createHash('sha256').update(read.bytes).digest('hex')}` };
}

// ---------------------------------------------------------------------------------------------
// Path derivations — pure with respect to their INPUTS, but subject to the same gates the asset
// directory is (a derived pathname is not by itself an ownership boundary): `{slug: "../elsewhere"}`
// escapes the provenance root exactly as it escapes the asset root.
// ---------------------------------------------------------------------------------------------

const PROVENANCE_DIRNAME = '.provenance';
const RUN_NAMESPACE = 'run';
const CHAPTERS_NAMESPACE = 'chapters';
const RUN_RECORD_NAME = 'current.json';
const PENDING_TOKEN_NAME = 'pending.json';

/**
 * `<publish.chapters_dir>/.provenance` — a plugin-owned root, physically disjoint from
 * `capture.output_dir` (verified by `assertProvenanceOwnership`, never assumed from the two keys'
 * names alone).
 *
 * @param {{publish: {chapters_dir: string}}} profileLike
 * @returns {string}
 */
export function provenanceRoot(profileLike) {
  return posixJoin(profileLike.publish.chapters_dir, PROVENANCE_DIRNAME);
}

/**
 * `<root>/chapters/<group>/<slug>.json` (grouped) or `<root>/chapters/<slug>.json` (flat). Stable
 * across W2, W5 and W6 — all three call this one derivation rather than keeping three private
 * copies that could disagree.
 *
 * @param {{publish: {chapters_dir: string}}} profileLike
 * @param {{slug: string|number, group?: string}} entry
 * @returns {string}
 */
export function chapterRecordPath(profileLike, entry) {
  const root = provenanceRoot(profileLike);
  const fileName = `${String(entry.slug)}.json`;
  // `entry.group !== undefined`, never a truthy check — chapter-paths.mjs's own convention
  // (chapterRelPath, outputDirTail), documented there as "a falsy-but-present group value must
  // never silently derive a flat path". A truthy check would treat `group: 0` (or `''`) as flat
  // here while chapterAssetDir treats the identical entry as grouped — a real cross-module
  // classification mismatch for a malformed-but-present manifest value (found by paths, #362).
  const tail = entry.group !== undefined ? posixJoin(String(entry.group), fileName) : fileName;
  return posixJoin(root, CHAPTERS_NAMESPACE, tail);
}

function runNamespaceDir(profileLike) {
  return posixJoin(provenanceRoot(profileLike), RUN_NAMESPACE);
}

function chaptersNamespaceDir(profileLike) {
  return posixJoin(provenanceRoot(profileLike), CHAPTERS_NAMESPACE);
}

function runRecordPath(profileLike) {
  return posixJoin(runNamespaceDir(profileLike), RUN_RECORD_NAME);
}

function pendingTokenPath(profileLike) {
  return posixJoin(runNamespaceDir(profileLike), PENDING_TOKEN_NAME);
}

// Canonicalize a possibly-not-yet-existing path for COMPARISON purposes: absolutize, lexically
// normalize, then canonicalize the LONGEST EXISTING PREFIX via a real `realpath` (resolving any
// symlink components already on disk, multi-hop, cycle-detected, relative targets resolved
// against the link's own parent — this is exactly what POSIX realpath(3) already guarantees, so
// delegating to `deps.realpathSync` gets those guarantees for free rather than re-deriving a
// component-by-component walker) and re-appends the not-yet-existing tail unchanged.
//
// Returns a DISCRIMINATED result rather than throwing or silently degrading: a symlink cycle
// (ELOOP) or any other inspection failure during the walk is a hazard a caller must be able to
// halt on, never a value it can accidentally compare as if resolution had succeeded.
function canonicalizeForComparison(rawPath, deps) {
  // A RELATIVE rawPath is never absolutized against a working directory read by THIS module —
  // `realpathSync` resolves a relative candidate against the real process working directory
  // internally, which needs no `process` reference in this file's own source (the capability
  // policy bans every such reference, in any shape, anywhere in this module).
  const absolute = isAbsolutePath(rawPath);
  const segments = normalizeSegments(rawSegments(rawPath), absolute);

  // The prefix path to probe at a given depth. Depth 0 means "no segments left at all": the
  // filesystem root for an absolute candidate, the working directory for a relative one (which
  // `realpathSync` resolves internally, so this module never reads a working directory itself).
  function prefixPath(depth) {
    if (depth === 0) return absolute ? '/' : '.';
    const joined = segments.slice(0, depth).join('/');
    return absolute ? `/${joined}` : joined;
  }

  let tail = [];
  let idx = segments.length;
  while (idx >= 0) {
    const candidate = prefixPath(idx);
    try {
      const real = deps.realpathSync(candidate); // always returns an absolute path
      const realSegments = normalizeSegments(rawSegments(real), true);
      return { ok: true, segments: realSegments.concat(tail) };
    } catch (err) {
      if (err.code === 'ENOENT' || err.code === 'ENOTDIR') {
        if (idx === 0) return { ok: true, segments: tail }; // nothing at all resolves; degrade gracefully
        tail = [segments[idx - 1], ...tail];
        idx -= 1;
        continue;
      }
      // ELOOP (a symlink cycle) or any other inspection failure — a hazard, not a value.
      return { ok: false, reason: err.code === 'ELOOP' ? 'symlink_cycle' : 'inspection_failure', path: candidate };
    }
  }
  return { ok: true, segments: tail };
}

// ---------------------------------------------------------------------------------------------
// Row 1 — gate 5: assertProvenanceOwnership. Called from W1 prose (operator-facing) AND
// unconditionally + silently at the top of every row-6/row-2 entrypoint (enforcement).
// ---------------------------------------------------------------------------------------------

/**
 * Verify that this profile's provenance root is physically disjoint from `capture.output_dir` — an
 * enforced namespace contract, not a naming convention (nothing in the shipped schema relates the
 * two keys, and the Obsidian adapter documents a supported FLAT topology where they are literally
 * the same directory).
 *
 * The overlap outcome is conditioned on whether the adopter asked for provenance
 * (`capture.build_identity` configured): configured ⇒ halt, naming both keys and their resolved
 * values; unconfigured ⇒ warn once and report `{ok:false, skip:true, warnings}` so every existing
 * flat-topology handbook keeps working without this release bricking it for owners who never
 * enabled the feature.
 *
 * @param {{capture: {output_dir: string, build_identity?: object}, publish: {chapters_dir: string}}} profileLike
 * @param {object} [deps]
 * @returns {{ok: true, root: string}|{ok: false, halts: Array<{halt: string, message: string}>}|{ok: false, skip: true, warnings: string[]}}
 */
export function assertProvenanceOwnership(profileLike, deps) {
  const d = mergeDeps(deps);
  const root = provenanceRoot(profileLike);
  const outputDir = profileLike.capture.output_dir;

  const rootResolved = canonicalizeForComparison(root, d);
  if (!rootResolved.ok) return haltResult('provenance_hazard', `cannot resolve provenance root '${root}': ${rootResolved.reason}`, { path: rootResolved.path });
  const outputResolved = canonicalizeForComparison(outputDir, d);
  if (!outputResolved.ok) return haltResult('provenance_hazard', `cannot resolve capture.output_dir '${outputDir}': ${outputResolved.reason}`, { path: outputResolved.path });
  const rootCanon = rootResolved.segments;
  const outputCanon = outputResolved.segments;

  const overlaps = isSegmentPrefixOf(rootCanon, outputCanon) || isSegmentPrefixOf(outputCanon, rootCanon);
  if (!overlaps) {
    return { ok: true, root };
  }

  const configured = profileLike.capture.build_identity != null;
  if (configured) {
    // The overlap decision above is made on the RESOLVED (realpath'd) segments — `root` and
    // `outputDir` below are the raw, as-configured strings, which is exactly what makes this halt
    // otherwise unactionable: a symlinked alias can make two raw paths look disjoint (different
    // names, no lexical prefix relationship) while they resolve into one overlapping tree, which is
    // the only reason this halt is firing at all. Naming just the raw strings tells the operator
    // "these two look fine to me" about the very halt refusing them. Rendering the resolved
    // segments back into absolute paths mirrors `canonicalizeForComparison`'s own contract
    // (`realpathSync` always returns an absolute path, so every resolved segment list is absolute).
    const rootResolvedPath = `/${rootCanon.join('/')}`;
    const outputResolvedPath = `/${outputCanon.join('/')}`;
    return {
      ok: false,
      halts: [
        {
          halt: 'provenance_root_overlap',
          message:
            `provenance root '${root}' (derived from publish.chapters_dir; resolves to ` +
            `'${rootResolvedPath}') overlaps capture.output_dir '${outputDir}' (resolves to ` +
            `'${outputResolvedPath}') — the capture command's writable mount cannot be allowed to ` +
            'reach the provenance tree, and vice versa. Relocate capture.output_dir so the two ' +
            'trees are disjoint, or remove capture.build_identity to stop asking for provenance on ' +
            'this topology.',
        },
      ],
    };
  }
  return {
    ok: false,
    skip: true,
    warnings: [
      `provenance skipped: root '${root}' overlaps capture.output_dir '${outputDir}' and ` +
        'capture.build_identity is not configured. No provenance records will be written for ' +
        'this profile.',
    ],
  };
}

// ---------------------------------------------------------------------------------------------
// Establishment — create the provenance root and its two fixed namespace directories, component
// by component, verifying each one a real directory (gate 6, establishment half).
// ---------------------------------------------------------------------------------------------

function establishHierarchy(profileLike, deps) {
  // `publish.chapters_dir` is the published docs tree, owned by the shipped workflow rather than
  // by this feature — it is NOT part of the gate-6 hazard-audited provenance namespace (that starts
  // at the root below). W2 can genuinely run before it exists on a brand-new handbook's very first
  // capture, so it is ensured here with an ordinary recursive `mkdir`, outside the individual-
  // component discipline gate 6 requires for the root/run/chapters components themselves.
  try {
    deps.mkdirSync(profileLike.publish.chapters_dir, { recursive: true });
  } catch (err) {
    if (err.code !== 'EEXIST') {
      return { ok: false, hazard: { kind: 'hazard', reason: 'inspection_failure', path: profileLike.publish.chapters_dir } };
    }
  }

  const root = provenanceRoot(profileLike);
  const components = [root, runNamespaceDir(profileLike), chaptersNamespaceDir(profileLike)];
  for (const dir of components) {
    const result = ensureDirComponent(dir, deps);
    if (!result.ok) return result;
  }
  return { ok: true };
}

function establishChapterGroupDir(profileLike, entry, deps) {
  if (entry.group === undefined) return { ok: true };
  const dir = posixJoin(chaptersNamespaceDir(profileLike), String(entry.group));
  return ensureDirComponent(dir, deps);
}

// ---------------------------------------------------------------------------------------------
// W2's preflight — gates 1-4 over the ASSET tree (not the provenance root; that is gates 5-6
// above). None of this existed before this pass: `openCaptureRun` derived and hashed every
// entry's asset directory with no validation at all, so a traversal slug or an inside-root alias
// produced confident provenance evidence for the wrong chapter — the exact defect this closes.
// Run at W2 (`openCaptureRun`, over the opening entry set) and re-run at W5/W6 (`recordChapterProvenance`
// /`buildProvenanceReport`, over the FULL accepted manifest, since a symlink can be planted between
// stages and gate 4 is a cross-entry recheck no single-entry call can perform).
// ---------------------------------------------------------------------------------------------

/**
 * Gates 1-4 over the asset tree for the FULL accepted entry set. The four predicates themselves —
 * `isValidSlugSyntax`, `findCanonicalPathCollisions`, `resolvePhysicalContainment`,
 * `findPhysicalPathCollisions` — are `paths`' (chapter-paths.mjs), imported rather than
 * vendored: an earlier version of this function carried its own alphabet regex and its own
 * containment walk built on `canonicalizeForComparison`/`deps.realpathSync`, which is exactly the
 * duplication this release has collapsed everywhere else (a second `isCanonicalAssetKey`, a
 * near-duplicate `expectedAssets`, a duplicated stripper). The realpath-based version was also
 * WRONG in a way tests alone could not surface: the plan requires gate 3's seam trace to be a
 * component-wise `lstat`/`readlink` walk with NO `realpath` call, specifically because a
 * `realpath`-based implementation passes every REFUSAL fixture (outside-target halts, two-hop
 * chains, relative escapes) while silently following a symlink whose target legitimately stays
 * INSIDE the root — the one POSITIVE case the gate exists to permit. A green suite built only from
 * refusal fixtures could not tell the two apart; only asserting the seam trace itself can. This
 * module still owns: the disk-touching side (wiring `deps.lstatSync`/`deps.readlinkSync` into gate
 * 3's `{lstat, readlink}` seam, so chapter-paths.mjs keeps importing nothing), and the halt-shaping
 * for each gate.
 *
 * @param {object} profileLike
 * @param {Array<{slug: string|number, group?: string}>} entries
 * @param {object} deps
 * @returns {{ok: true}|{ok: false, halts: Array<object>}}
 */
function validateEntriesForCapture(profileLike, entries, deps) {
  // Gate 1 — slug AND group alphabet. This module is independently callable (W5/W6 do not assume
  // some earlier W1 step already validated the manifest), so it re-asserts format itself.
  for (const entry of entries) {
    // `isValidSlugSyntax` itself already rejects a non-string outright (`typeof slug === 'string'`)
    // — but this caller used to run it against `String(entry.slug)`, a value ALREADY coerced to a
    // string before the type check could ever see the original. `{slug: 1}` therefore reached
    // `String(1) === '1'`, which passes the kebab-alphabet regex (digits are in the class), and the
    // non-string entry sailed past gate 1 entirely — a W6 probe with a numeric slug reached
    // `chapter_read_failed` (file "1.md" not found) instead of `invalid_slug`. The RAW value must
    // reach the syntax check; `slugStr`/`String(entry.group)` below are for the halt MESSAGE only.
    const slugStr = String(entry.slug);
    if (!isValidSlugSyntax(entry.slug)) {
      return haltResult('invalid_slug', `entry slug '${slugStr}' does not match the required kebab-case alphabet`, { slug: slugStr });
    }
    if (entry.group !== undefined && !isValidSlugSyntax(entry.group)) {
      return haltResult('invalid_group', `entry group '${String(entry.group)}' does not match the required kebab-case alphabet`, {
        group: String(entry.group),
      });
    }
  }

  // Gate 2 — canonical (lexical) uniqueness.
  const canonicalCollisions = findCanonicalPathCollisions(profileLike, entries);
  if (canonicalCollisions.length > 0) {
    const first = canonicalCollisions[0];
    return haltResult(
      'duplicate_asset_dir',
      `two or more entries derive the identical asset directory '${first.canonicalPath}' (slugs ${first.entries.map((e) => `'${e.slug}'`).join(', ')})`,
      { assetDir: first.canonicalPath },
    );
  }

  // Gate 3 — physical containment, per entry, via a component-wise lstat/readlink walk (never
  // realpath) — deps.lstat/deps.readlink are THIS module's own seam, so chapter-paths.mjs stays
  // dependency-free. `inspection-failed` is routed to a `provenance_hazard` halt, never silently
  // read as "not a symlink" (the same distinction gate 6 already makes).
  //
  // `resolvePhysicalContainment`'s `rootDir` argument is used AS GIVEN — only lexically
  // normalized, never itself walked against the real filesystem (only `dir` is). The CALLER is
  // therefore responsible for supplying an already-canonical root: a raw, unresolved
  // `capture.output_dir` can sit behind an OS-level symlinked ancestor (macOS's `/tmp` ->
  // `/private/tmp`, `/var` -> `/private/var`), which the per-entry walk below WILL resolve through
  // (it walks every component of the CANDIDATE path), producing a spurious `escapes-root` against
  // a root that never went through the same resolution. Canonicalizing the root HERE, ONCE, via
  // the real filesystem (the same mechanism gate 5 already uses for its own disjointness check)
  // aligns both sides onto one coordinate system. This is a single root-level canonicalization,
  // not the per-entry symlink-following walk the plan requires to stay realpath-free — it does not
  // touch the guarantee that requirement protects (an entry-level symlink whose target legitimately
  // stays inside the root is still resolved component-by-component, never via realpath).
  const outputRootResolved = canonicalizeForComparison(profileLike.capture.output_dir, deps);
  if (!outputRootResolved.ok) {
    return haltResult('provenance_hazard', `cannot resolve capture.output_dir: ${outputRootResolved.reason}`, { path: outputRootResolved.path });
  }
  const canonicalOutputRoot = `/${outputRootResolved.segments.join('/')}`;
  // `resolvePhysicalContainment` requires `rootDir` and `dir` to share ONE rootedness — it treats a
  // mismatch (one absolute, one relative) the same as a genuine escape, halting `escapes-root`
  // unconditionally. `canonicalOutputRoot` above is ALWAYS absolute (canonicalizeForComparison's
  // contract), so the per-entry candidate must be derived from that SAME absolute root rather than
  // from the raw (possibly relative) `capture.output_dir` — otherwise a profile whose output_dir and
  // chapters_dir are BOTH relative (the shipped example profile's own topology:
  // `output_dir: "vault/handbook/assets"`, `chapters_dir: "vault/handbook"`) halts every entry here,
  // in open, W5 and W6 alike (all three route through this one function), on the very first real
  // capture. Swapping in the canonical root for THIS derivation only still runs gate 3's own
  // component-wise lstat/readlink walk over the resulting path unchanged (no realpath call is
  // skipped or added) — it only fixes which coordinate system `dir` is expressed in.
  const canonicalProfileForAssetDir = {
    ...profileLike,
    capture: { ...profileLike.capture, output_dir: canonicalOutputRoot },
  };

  const containmentDeps = {
    lstat: (p) => deps.lstatSync(p),
    readlink: (p) => deps.readlinkSync(p),
  };
  const resolvedEntries = [];
  for (const entry of entries) {
    const assetDir = chapterAssetDir(canonicalProfileForAssetDir, entry);
    // `resolvePhysicalContainment` treats ANY lstat failure while walking `dir`'s components as
    // `inspection-failed` — correct for a genuine hazard, but a chapter's asset directory
    // legitimately does not exist yet on its very first capture run (openCaptureRun snapshots the
    // OPENING baseline before the capture command has written anything there at all). Absent is
    // therefore checked and skipped HERE, at this call site, before gate 3 ever runs on it —
    // matching this module's own established rule elsewhere (gate 6's `ensureDirComponent`/
    // `inspectDirComponent`) that ENOENT-on-a-not-yet-established path is expected, not a hazard,
    // while any OTHER lstat failure still is. Nothing to check also means nothing to add to gate
    // 4's cross-entry collision set (two directories that do not yet exist cannot physically
    // collide).
    let assetDirExists = true;
    try {
      deps.lstatSync(assetDir);
    } catch (err) {
      if (err.code === 'ENOENT' || err.code === 'ENOTDIR') {
        assetDirExists = false;
      } else {
        return haltResult('provenance_hazard', `cannot inspect asset directory '${assetDir}': ${err.code ?? err.message}`, { assetDir });
      }
    }
    if (!assetDirExists) continue;
    const result = resolvePhysicalContainment(canonicalOutputRoot, assetDir, containmentDeps);
    if (!result.ok) {
      if (result.halt.reason === 'inspection-failed') {
        return haltResult('provenance_hazard', result.halt.detail, { assetDir });
      }
      if (result.halt.reason === 'cycle') {
        return haltResult('symlink_cycle', result.halt.detail, { assetDir });
      }
      return haltResult('asset_dir_escapes_output_dir', result.halt.detail, { assetDir, slug: entry.slug });
    }
    resolvedEntries.push({ entry, resolved: result.resolved });
  }

  // Gate 4 — pairwise PHYSICAL uniqueness, over gate 3's own resolved output (never re-derived) —
  // the cross-entry recheck `acceptedEntries` exists for.
  const physicalCollisions = findPhysicalPathCollisions(resolvedEntries);
  if (physicalCollisions.length > 0) {
    const first = physicalCollisions[0];
    return haltResult(
      'physical_asset_dir_collision',
      `two or more entries resolve to the same PHYSICAL asset directory '${first.resolvedPath}' (slugs ${first.entries.map((e) => `'${e.slug}'`).join(', ')})`,
      { resolvedPath: first.resolvedPath },
    );
  }

  return { ok: true };
}

// ---------------------------------------------------------------------------------------------
// Row 2 — openCaptureRun. Reservation is an EXCLUSIVE create on the final pending-token name
// (O_CREAT | O_EXCL | O_NOFOLLOW), never a check-then-rename — the contended name is the fixed
// final one, so a temp-then-rename would protect nothing here.
// ---------------------------------------------------------------------------------------------

function haltResult(halt, message, extra) {
  return { ok: false, halts: [{ halt, message, ...extra }] };
}

// Best-effort cleanup of a temp this call itself created, on ITS OWN failure path (a write or
// rename that this function caught and is about to halt on) — a CAUGHT, handled failure is not the
// same situation row 6's crash-recovery model exists for (a process that died mid-operation with
// no chance to clean up after itself); when this code is still running and about to return a halt,
// it can and should remove what it just wrote rather than leaving litter for the operator to find
// later (codex, important #6 — "zero surviving temps"). A secondary failure here is swallowed —
// if the temp cannot be removed, row 6's `prepared`/`orphan_temp` states and their repairs are
// exactly the fallback for that.
function unlinkBestEffort(path, deps) {
  try {
    deps.unlinkSync(path);
  } catch {
    /* best-effort only; row 6's repair states cover a temp this cleanup itself could not remove */
  }
}

// Best-effort `closeSync` for a descriptor whose caller ALREADY has a definitive result (or error)
// to return — a symlink/non-regular/hard-link hazard already classified in `openLeafNoFollow`, a
// write failure already caught and about to become a halt, or a probe read whose bytes are already
// in hand. A throwing `close()` here must never escape uncaught and must never MASK the result the
// caller is already holding: a `finally`/`catch` body that itself throws SILENTLY REPLACES whatever
// exception was already propagating (codex round 3, "a cleanup closeSync failure survives") — so
// every one of those call sites swallows a close failure here rather than letting `deps.closeSync`
// run unguarded.
function closeBestEffort(fd, deps) {
  try {
    deps.closeSync(fd);
  } catch {
    /* best-effort only; see the comment above — the caller's own result/error already stands */
  }
}

// `writeSync` returning fewer bytes than requested is a real possibility (a full disk, a pipe, an
// interrupted write) — not merely a hypothetical interposed test seam — and every writer in this
// module ignored the returned byte count outright: a seam that persists one byte and returns 1 let
// `closeCaptureRun` rename a one-byte "{" temp into place as a successfully committed run record.
// Throws rather than returning a result, so every existing call site's surrounding try/catch (which
// already handles a THROWING `writeSync` for cleanup purposes) handles a short write identically,
// with no separate branch needed at each of the three call sites.
class ShortWriteError extends Error {
  constructor(expected, actual) {
    super(`short write: wrote ${actual} of ${expected} bytes`);
    this.reason = 'short_write';
    this.expected = expected;
    this.actual = actual;
  }
}

function writeFull(fd, buffer, deps) {
  const written = deps.writeSync(fd, buffer);
  if (written !== buffer.length) {
    throw new ShortWriteError(buffer.length, written);
  }
}

/**
 * Open a capture run: re-assert ownership (silently), establish the provenance hierarchy,
 * snapshot every entry's current asset-dir hashes as the OPENING baseline, resolve the opening
 * build identity, and reserve a one-shot pending token holding the run id and a digest of the
 * opening payload (never the snapshot itself — the snapshot travels in the returned `runState`,
 * which is what the cross-process serialization test protects).
 *
 * @param {object} profileLike
 * @param {Array<{slug: string|number, group?: string}>} entries
 * @param {import('./build-identity.mjs').UiReadObservation|null} [openingObservation]
 * @param {object} [deps]
 * @returns {{ok: true, runState: object}|{ok: false, halts: Array<object>}|{needs_ui_read: true, region_hint: string}}
 */
export function openCaptureRun(profileLike, entries, openingObservation, deps) {
  const d = mergeDeps(deps);

  const ownership = assertProvenanceOwnership(profileLike, d);
  if (ownership.skip) {
    return { ok: true, runState: { skipped: true } };
  }
  if (!ownership.ok) {
    return { ok: false, halts: ownership.halts };
  }

  const established = establishHierarchy(profileLike, d);
  if (!established.ok) return { ok: false, halts: [established.hazard] };

  const validated = validateEntriesForCapture(profileLike, entries, d);
  if (!validated.ok) return validated;

  const buildIdentity = profileLike.capture.build_identity ?? null;
  const uiReadEnabled = buildIdentity?.ui_read !== false;
  let commandOutcome = null;
  if (buildIdentity?.command) {
    commandOutcome = d.runIdentityCommand(buildIdentity.command);
  }
  const opening = resolveBuildIdentity({ commandOutcome, uiReadEnabled, uiObservation: openingObservation });
  if (opening.needs_ui_read) return opening;

  // Snapshotting is an I/O-heavy walk of caller-controlled directories — `snapshotAssetHashes`
  // catches ENOENT/ENOTDIR internally (an absent directory is legitimately an empty map) but
  // re-throws anything else, and an earlier version of this function had NOTHING catching that,
  // so an unexpected errno (EACCES, EIO, ...) crashed the whole call with an uncaught exception
  // instead of returning a halt (codex, important #6).
  const openingAssets = {};
  try {
    for (const entry of entries) {
      const assetDir = chapterAssetDir(profileLike, entry);
      openingAssets[chapterKeyFor(entry)] = snapshotAssetHashes(assetDir, d);
    }
  } catch (err) {
    return haltResult('provenance_hazard', `cannot snapshot the opening asset hashes: ${err.code ?? err.message}`, {});
  }

  const runState = {
    skipped: false,
    run_id: d.randomUUID(),
    opening,
    opening_assets: openingAssets,
    entries: entries.map(entryKeyShape),
  };
  const digest = digestOpeningPayload(openingPayloadFromRunState(runState));
  runState.opening_digest = digest;

  const tokenText = JSON.stringify({ run_id: runState.run_id, opening_digest: digest });
  const tokenPath = pendingTokenPath(profileLike);
  let fd;
  try {
    fd = d.openSync(tokenPath, fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_WRONLY | fs.constants.O_NOFOLLOW, 0o644);
  } catch (err) {
    if (err.code === 'EEXIST') {
      return haltResult('run_already_open', 'a capture run is already open for this profile — close or abort it before opening a new one.');
    }
    return haltResult('provenance_hazard', `cannot create the pending token: ${err.code ?? err.message}`, { path: tokenPath });
  }
  try {
    writeFull(fd, Buffer.from(tokenText, 'utf8'), d);
  } catch (err) {
    // A throwing (or short) write here previously escaped this function entirely — the surrounding
    // `try` had only a `finally` closing the fd, never a `catch`, so an ordinary write failure
    // became an UNCAUGHT exception instead of the returned `{ok:false, halts}` this module's
    // contract promises everywhere else, and the just-created (O_CREAT|O_EXCL) token was left
    // behind with no caller ever having a chance to clean it up. Best-effort: this closeSync
    // failing must never MASK the write failure we are about to report (codex round 3).
    closeBestEffort(fd, d);
    unlinkBestEffort(tokenPath, d);
    return haltResult('provenance_hazard', `cannot write the pending token: ${err.reason ?? err.code ?? err.message}`, { path: tokenPath });
  }
  try {
    d.closeSync(fd);
  } catch (err) {
    // The token was fully written (writeFull succeeded) but a failing close means it cannot be
    // trusted as durably flushed — untrust it outright rather than returning ok:true over an
    // uncertain token (some filesystems can fail a close after acknowledging the write).
    unlinkBestEffort(tokenPath, d);
    return haltResult('provenance_hazard', `cannot close the pending token after writing it: ${err.code ?? err.message}`, { path: tokenPath });
  }

  return { ok: true, runState };
}

// chapter-paths.mjs's outputDirTail checks `entry.group !== undefined` (strict) — a `group: null`
// entry is therefore treated as a GROUPED entry named "null", not a flat one. So `group` must be
// OMITTED entirely for a flat entry, never coerced to `null` — and since `JSON.stringify` drops an
// `undefined`-valued property outright, omitting the key is also what survives the cross-process
// serialization boundary unchanged.
function entryKeyShape(entry) {
  return entry.group !== undefined ? { slug: entry.slug, group: entry.group } : { slug: entry.slug };
}

function chapterKeyFor(entry) {
  return entry.group !== undefined ? `${entry.group}/${entry.slug}` : String(entry.slug);
}

// The EXACT shape `digestOpeningPayload` was originally computed over, reconstructed from a
// `runState` rather than re-derived independently at each call site — `openCaptureRun` builds it
// once at creation, `closeCaptureRun` rebuilds the identical shape from `runState`'s own fields to
// RE-VERIFY the token's stored digest. One shared shape is what keeps the two from silently
// drifting into two different notions of "the opening payload".
function openingPayloadFromRunState(runState) {
  return { entries: runState.entries, assets: runState.opening_assets, identity: runState.opening };
}

// The one recursive asset-tree walk both sweeps below share (the hash snapshot and the filename
// listing) — a single definition of "which files under an asset directory this feature can see",
// rather than two copies free to drift apart on the symlink or the errno rule. A symlink is never
// followed, as a directory to descend or as a file to visit. ENOENT/ENOTDIR ends that branch
// quietly (an asset directory that does not exist yet is legitimately empty — W2 snapshots the
// opening baseline before the capture command has written anything); every OTHER errno propagates
// to the caller, which turns it into a halt rather than a silently short list.
function walkRegularFiles(rootDir, deps, visit) {
  walk(rootDir, '');

  function walk(absDir, relPrefix) {
    let entries;
    try {
      entries = deps.readdirSync(absDir, { withFileTypes: true });
    } catch (err) {
      if (err.code === 'ENOENT' || err.code === 'ENOTDIR') return;
      throw err;
    }
    for (const dirent of entries) {
      const childAbs = posixJoin(absDir, dirent.name);
      const childRel = relPrefix ? `${relPrefix}/${dirent.name}` : dirent.name;
      if (dirent.isSymbolicLink()) continue;
      if (dirent.isDirectory()) walk(childAbs, childRel);
      else if (dirent.isFile()) visit(childAbs, childRel);
    }
  }
}

// `capture.output_dir` is NOT plugin-owned (ledger row 7: the opaque capture command's own
// namespace, "outside our contract entirely"), so it is not part of gate 6's stated obligation set
// (scoped to the token/record/temp leaves and the root/run/ hierarchy). But an UNPROTECTED read
// here is a real substitution vector, not merely an untidy inconsistency: `readdirSync`'s dirent
// types are a snapshot at listing time, so a regular file the walk just classified as `isFile()`
// can be replaced by a symlink before this function ever opens it — a race window, not a
// hypothetical, and the other two asset-hash call sites in this module (rule 5's rehash, W6's
// current-hash resolution) already close it via `hashFileNoFollow`. Using the SAME helper here
// keeps that closed consistently rather than leaving one of three call sites unprotected. A hazard
// (or the file vanishing between listing and open) is treated exactly like `isSymbolicLink()`
// already is a few lines up: the entry is silently excluded from the snapshot rather than halting
// the whole run — this tree is not ours to halt on, but it is also not ours to hash blindly through
// a symlink. The chapter-level consequence is the existing, correct one: a missing expected image
// fails completeness (rule 3) and the chapter is reported ineligible, never silently trusted.
function snapshotAssetHashes(assetDir, deps) {
  const result = Object.create(null);
  walkRegularFiles(assetDir, deps, (absPath, relPath) => {
    const hashed = hashFileNoFollow(absPath, deps);
    if (hashed.kind === 'present') result[relPath] = hashed.digest;
    // 'hazard' (a symlink/hard-link/non-regular swapped in after the listing, or an inspection
    // failure) and 'absent' (the file vanished) are both excluded, same as a symlink dirent.
  });
  return result;
}

// ---------------------------------------------------------------------------------------------
// Row 3 — closeCaptureRun. Prepare a process-unique temp, commit by rename, then remove the token
// AND every leftover matching temp (a committed run can have leftover temps from a retried write,
// and cleanup — here and in `cleanupCommittedRun` — must own them).
// ---------------------------------------------------------------------------------------------

function tempRunRecordPath(profileLike, deps) {
  return posixJoin(runNamespaceDir(profileLike), `${RUN_RECORD_NAME}.${deps.randomUUID()}.tmp`);
}

// Returns {ok: true, temps: string[]} | {ok: false, hazard: {kind:'hazard', reason, path}} — never
// throws. A non-ENOENT `readdirSync` failure (EIO, ...) previously rethrew uncaught (codex round
// 3): this is called from `closeCaptureRun` AFTER its rename has already committed the run record,
// so a caller must be able to distinguish "no temps to report" from "could not find out" rather
// than crash either way.
function listMatchingTemps(profileLike, deps) {
  const dir = runNamespaceDir(profileLike);
  let entries;
  try {
    entries = deps.readdirSync(dir);
  } catch (err) {
    if (err.code === 'ENOENT') return { ok: true, temps: [] };
    return { ok: false, hazard: { kind: 'hazard', reason: 'inspection_failure', path: dir } };
  }
  const prefix = `${RUN_RECORD_NAME}.`;
  const suffix = '.tmp';
  const temps = entries.filter((name) => name.startsWith(prefix) && name.endsWith(suffix)).map((name) => posixJoin(dir, name));
  return { ok: true, temps };
}

/**
 * Close a capture run: re-verify the token matches this `runState`, snapshot the CLOSING asset
 * hashes, resolve the closing build identity and the run's final recorded identity, write the run
 * record to a process-unique temp under `run/`, commit by rename, then remove the token and every
 * leftover matching temp. Never throws on an ordinary failure — every exit is a returned
 * `{ok:false, halts}`, so a caller branches on `halts` rather than relying on an exception.
 *
 * @param {object} profileLike
 * @param {object} runState
 * @param {{ok: boolean, detail?: string}} captureOutcome
 * @param {import('./build-identity.mjs').UiReadObservation|null} [closingObservation]
 * @param {object} [deps]
 * @returns {{ok: true, runState: object, warnings: string[]}|{ok: false, halts: Array<object>}|{needs_ui_read: true, region_hint: string}}
 */
export function closeCaptureRun(profileLike, runState, captureOutcome, closingObservation, deps) {
  const d = mergeDeps(deps);

  const ownership = assertProvenanceOwnership(profileLike, d);
  if (ownership.skip) return { ok: true, runState: { skipped: true }, warnings: [] };
  if (!ownership.ok) return { ok: false, halts: ownership.halts };

  if (runState.skipped) return { ok: true, runState, warnings: [] };

  const hierarchyHazard = inspectRunHierarchyComponents(profileLike, d);
  if (hierarchyHazard) return { ok: false, halts: [{ halt: 'provenance_hazard', ...hierarchyHazard }] };

  const tokenPath = pendingTokenPath(profileLike);
  const tokenRead = readLeafText(tokenPath, d);
  if (tokenRead.kind === 'hazard') return haltResult('provenance_hazard', 'token hazard', { path: tokenRead.path, reason: tokenRead.reason });
  if (tokenRead.kind === 'absent') {
    return haltResult('token_missing', 'no pending token found for this run — it may already have been closed or aborted.');
  }
  const parsedToken = parseJsonStrict(tokenRead.text);
  // Optional-chained for the same reason as the equivalent read in `recoverProvenanceState`: a
  // token body of the literal `null` parses successfully into `{ok: true, value: null}` and is the
  // one JSON value that is both non-object and dereferenceable-looking, so a bare `.run_id` threw
  // a TypeError here — in a function whose contract is that every ordinary failure comes back as a
  // returned `{ok: false, halts}`. It is a `stale_replay` like every other non-matching token.
  // Line 1199 below needs no such guard: it is reachable only once `run_id` compared equal to a
  // string, which a null value cannot do.
  if (!parsedToken.ok || parsedToken.value?.run_id !== runState.run_id) {
    return haltResult('stale_replay', 'the token on disk does not match this runState — this run has already moved on; re-derive with recoverProvenanceState.');
  }
  // The digest is RECOMPUTED from runState's actual current content and checked against the
  // TOKEN's stored value on disk — never against `runState.opening_digest`, which is just as
  // tamperable as the fields it is supposed to be vouching for. `run_id` alone only proves this
  // runState claims the right identity; it says nothing about whether `entries`/`opening`/
  // `opening_assets` still match what was opened. A serialized `runState` whose payload was
  // mutated while its two scalar fields were left untouched is exactly what this closes: the token
  // on disk — the one thing an attacker did not get to also rewrite — is the sole source of truth
  // for what the opening payload was allowed to be.
  const recomputedDigest = digestOpeningPayload(openingPayloadFromRunState(runState));
  if (recomputedDigest !== parsedToken.value.opening_digest) {
    return haltResult(
      'stale_replay',
      'this runState\'s opening payload does not match the token\'s stored digest — the payload was altered after opening, or this runState belongs to a different (possibly no-longer-open) run.',
    );
  }

  const buildIdentity = profileLike.capture.build_identity ?? null;
  const uiReadEnabled = buildIdentity?.ui_read !== false;
  // The closing observation runs the SAME three-step resolution order as the opening one — the
  // identity command is re-invoked, not skipped, since a command-configured profile must resolve
  // its closing identity from the command too, not fall straight to the UI-read fallback.
  let closingCommandOutcome = null;
  if (buildIdentity?.command) {
    closingCommandOutcome = d.runIdentityCommand(buildIdentity.command);
  }
  const closing = resolveBuildIdentity({
    commandOutcome: closingCommandOutcome,
    uiReadEnabled,
    uiObservation: closingObservation,
  });
  if (closing.needs_ui_read) return closing;

  // Same reasoning as the opening sweep: an unexpected errno during the closing sweep must return
  // a halt, not crash — and doing so HERE (before any temp is written) is what keeps the existing
  // "no record written before the closing resolution" guarantee true on this exit too.
  const closingAssets = {};
  try {
    for (const entry of runState.entries) {
      const assetDir = chapterAssetDir(profileLike, entry);
      closingAssets[chapterKeyFor(entry)] = snapshotAssetHashes(assetDir, d);
    }
  } catch (err) {
    return haltResult('provenance_hazard', `cannot snapshot the closing asset hashes: ${err.code ?? err.message}`, {});
  }

  const finalIdentity = resolveClosingIdentity({
    opening: runState.opening,
    captureOutcome,
    closing,
  });

  const chapters = {};
  for (const entry of runState.entries) {
    const key = chapterKeyFor(entry);
    chapters[key] = {
      opening: runState.opening_assets[key] ?? {},
      closing: closingAssets[key] ?? {},
    };
  }

  const record = {
    record_version: 1,
    run_id: runState.run_id,
    // The RECOMPUTED digest — the value just verified against the on-disk token above — never
    // `runState.opening_digest` directly. `runState` is caller-held data: mutating only its
    // `opening_digest` field (leaving `entries`/`opening`/`opening_assets` untouched) sails straight
    // through the check above unnoticed, because `recomputedDigest` is derived from the PAYLOAD, not
    // from this field. Writing that field's raw value into the record would land a forged digest in
    // a run that otherwise committed cleanly, which a later `recoverProvenanceState` read back as
    // authentic — the token is the sole authenticated source of truth for this value, and
    // `recomputedDigest` IS that authenticated value, already proven to match it above.
    opening_digest: recomputedDigest,
    build_identity: finalIdentity,
    chapters,
  };
  const recordText = JSON.stringify(record, null, 2);

  const tempPath = tempRunRecordPath(profileLike, d);
  const finalPath = runRecordPath(profileLike);

  let fd;
  try {
    fd = d.openSync(tempPath, fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_WRONLY | fs.constants.O_NOFOLLOW, 0o644);
    writeFull(fd, Buffer.from(recordText, 'utf8'), d);
  } catch (err) {
    if (fd !== undefined) {
      // Best-effort: this closeSync failing must never MASK the write failure we are about to
      // report (a throwing catch body would silently replace it — codex round 3).
      closeBestEffort(fd, d);
      unlinkBestEffort(tempPath, d); // a create that succeeded but a write that failed leaves a
      // partial temp on disk — remove it rather than leaving litter for the failure path to answer for.
    }
    return haltResult('provenance_hazard', `cannot write the closing temp: ${err.code ?? err.message}`, { path: tempPath });
  }
  try {
    d.closeSync(fd);
  } catch (err) {
    // A close failure right here means the fully-written temp is not yet renamed to its final
    // name — nothing durable has been committed at this point, so this is an ordinary halt (with
    // best-effort cleanup of the temp), not the post-commit case the cleanup loop below answers to.
    unlinkBestEffort(tempPath, d);
    return haltResult('provenance_hazard', `cannot close the closing temp after writing it: ${err.code ?? err.message}`, { path: tempPath });
  }

  try {
    d.renameSync(tempPath, finalPath);
  } catch (err) {
    unlinkBestEffort(tempPath, d); // the rename itself failed — the fully-written temp is still at
    // its OWN name, never at finalPath, so removing it leaves zero surviving temps on this exit.
    return haltResult('provenance_hazard', `cannot commit the run record: ${err.code ?? err.message}`, { path: finalPath });
  }

  // Cleanup: every leftover matching temp first, the token last — the same order and for the same
  // reason as the row-6 repairs. Both are best-effort: a leftover temp is still classified
  // correctly by recoverProvenanceState, and the token may already be gone under a concurrent or
  // retried close, which is not this call's failure. The rename above has ALREADY committed the run
  // record durably — a hazard while merely LISTING leftover temps for cleanup must never be
  // reported as if nothing was written (codex round 3): it is a WARNING on this still-`ok:true`
  // result, never a halt, and any leftover temp is still correctly seen by `recoverProvenanceState`
  // on its own next run regardless of whether this cleanup pass could enumerate it.
  const warnings = [];
  const tempsListed = listMatchingTemps(profileLike, d);
  if (tempsListed.ok) {
    for (const temp of tempsListed.temps) {
      unlinkBestEffort(temp, d);
    }
  } else {
    warnings.push(
      `the run record committed successfully, but leftover temps under 'run/' could not be listed for cleanup (${tempsListed.hazard.reason} at '${tempsListed.hazard.path}') — run recoverProvenanceState to check for and remove any leftover temp.`,
    );
  }
  unlinkBestEffort(tokenPath, d);

  return { ok: true, runState: { ...runState, closed: true }, warnings };
}

// ---------------------------------------------------------------------------------------------
// Record readers — field-by-field schema validation, never a bare JSON.parse + spot check. Both
// readers run the SAME shared `build_identity` validity check (`isValidBuildIdentityField`) and
// the SAME structural key predicate for every hash map — `isCanonicalAssetKey`, imported from
// chapter-paths.mjs rather than re-implemented here: rejects a leading '/', an empty segment, and
// '.'/'..' segments — constrains NO characters, since keys come from W2's own directory snapshot,
// and a reader rejecting what its own writer wrote is the defect this shared predicate avoids.
// ---------------------------------------------------------------------------------------------

// "A plain object" — non-null, not an array. The shape test every record reader below opens with,
// and the one a stored hash map must pass before its keys are examined.
function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function validateHashMap(map) {
  if (!isPlainObject(map)) return false;
  for (const key of Object.keys(map)) {
    if (!isCanonicalAssetKey(key)) return false;
    if (typeof map[key] !== 'string' || !HASH_GRAMMAR.test(map[key])) return false;
  }
  return true;
}

/**
 * Validate and parse a run record's raw text. Fails closed on: unparseable/duplicate-key/lone-
 * surrogate JSON; non-1 `record_version`; missing/non-string `run_id`; a malformed
 * `opening_digest`; an invalid `build_identity` sub-object; a non-object `chapters`, or any chapter
 * entry that is not an object, is missing `opening`/`closing`, or holds a non-canonical key or a
 * non-hash-grammar value in either map.
 *
 * @param {string} text
 * @returns {{ok: true, record: object}|{ok: false, reason: string}}
 */
export function readRunRecordText(text) {
  const parsed = parseJsonStrict(text);
  if (!parsed.ok) return { ok: false, reason: parsed.reason };
  const record = parsed.value;
  if (!isPlainObject(record)) return { ok: false, reason: 'not_an_object' };
  if (record.record_version !== 1) return { ok: false, reason: 'bad_record_version' };
  if (typeof record.run_id !== 'string') return { ok: false, reason: 'bad_run_id' };
  if (!isValidDigest(record.opening_digest)) return { ok: false, reason: 'bad_opening_digest' };
  const identityCheck = isValidBuildIdentityField(record.build_identity);
  if (!identityCheck.ok) return { ok: false, reason: `bad_build_identity:${identityCheck.reason}` };
  if (!isPlainObject(record.chapters)) return { ok: false, reason: 'bad_chapters' };
  for (const key of Object.keys(record.chapters)) {
    const entry = record.chapters[key];
    if (!isPlainObject(entry)) return { ok: false, reason: 'bad_chapter_entry' };
    if (!Object.hasOwn(entry, 'opening') || !Object.hasOwn(entry, 'closing')) {
      return { ok: false, reason: 'bad_chapter_entry' };
    }
    if (!validateHashMap(entry.opening) || !validateHashMap(entry.closing)) {
      return { ok: false, reason: 'bad_chapter_hash_map' };
    }
  }
  return { ok: true, record };
}

/**
 * Validate and parse a chapter record's raw text — the same identity checks plus `asset_hashes`.
 *
 * @param {string} text
 * @returns {{ok: true, record: object}|{ok: false, reason: string}}
 */
export function readChapterRecordText(text) {
  const parsed = parseJsonStrict(text);
  if (!parsed.ok) return { ok: false, reason: parsed.reason };
  const record = parsed.value;
  if (!isPlainObject(record)) return { ok: false, reason: 'not_an_object' };
  if (!Number.isInteger(record.record_version) || record.record_version < 1) {
    return { ok: false, reason: 'bad_record_version' };
  }
  // A version other than the one this reader understands is read back MINIMALLY — only far enough
  // to know the version itself is well-formed — and its OTHER fields are never validated against
  // v1's rules, which would be meaningless for a version this reader was not written for. This is
  // the one thing that makes `record_unsupported_version` a REACHABLE report state rather than a
  // branch nothing can ever take: an earlier version of this function rejected any non-1 version
  // outright as `malformed`, which is indistinguishable from genuine corruption to W6's delta
  // classifier and dead code in the caller that branches on the two separately.
  if (record.record_version !== 1) {
    return { ok: true, record, unsupportedVersion: true };
  }
  if (typeof record.run_id !== 'string') return { ok: false, reason: 'bad_run_id' };
  const identityCheck = isValidBuildIdentityField(record.build_identity);
  if (!identityCheck.ok) return { ok: false, reason: `bad_build_identity:${identityCheck.reason}` };
  if (record.detail !== undefined && typeof record.detail !== 'string') {
    return { ok: false, reason: 'bad_detail' };
  }
  if (!validateHashMap(record.asset_hashes)) return { ok: false, reason: 'bad_asset_hashes' };
  return { ok: true, record };
}

function readRunRecordFromDisk(profileLike, deps) {
  const read = readLeafText(runRecordPath(profileLike), deps);
  if (read.kind !== 'present') return read;
  const validated = readRunRecordText(read.text);
  if (!validated.ok) return { kind: 'invalid', reason: validated.reason };
  return { kind: 'present', record: validated.record };
}

function readChapterRecordFromDisk(profileLike, entry, deps) {
  const read = readLeafText(chapterRecordPath(profileLike, entry), deps);
  if (read.kind !== 'present') return read;
  const validated = readChapterRecordText(read.text);
  if (!validated.ok) return { kind: 'invalid', reason: validated.reason };
  return { kind: 'present', record: validated.record };
}

// ---------------------------------------------------------------------------------------------
// Row 4 — recordChapterProvenance. Applies the completeness rule; abstains (writes nothing, keeps
// whatever record already existed) on ANY failure, at every exit.
// ---------------------------------------------------------------------------------------------

/**
 * Record one chapter's provenance at W5, applying the completeness rule (rules 1-5 of the plan):
 * the run record verifies and its `run_id` matches `expectedRunId`; the chapter embeds at least
 * one in-directory image and no foreign one; every expected image appears in `closing`; every
 * expected image's `closing` hash differs from `opening`; and a fresh re-hash right now still
 * differs from `opening`. Any failure ⇒ no record written, the chapter's existing record (if any)
 * is left byte-identical, and the failing rule is returned as a warning.
 *
 * @param {object} profileLike
 * @param {Array<object>} acceptedEntries  the complete accepted manifest (gate 4 is a cross-entry recheck)
 * @param {object} entry
 * @param {string} chapterFile
 * @param {string} expectedRunId
 * @param {object} [deps]
 * @returns {{recorded: true, reason: null}|{recorded: false, reason: string}|{ok: false, halts: Array<object>}}
 */
export function recordChapterProvenance(profileLike, acceptedEntries, entry, chapterFile, expectedRunId, deps) {
  const d = mergeDeps(deps);

  const ownership = assertProvenanceOwnership(profileLike, d);
  if (ownership.skip) return { recorded: false, reason: 'provenance_skipped' };
  if (!ownership.ok) return { ok: false, halts: ownership.halts };

  // Gates 1-4 are re-run here over the COMPLETE accepted manifest — gate 4 (pairwise physical
  // uniqueness) is a cross-entry recheck no single-`entry` call could perform, which is exactly why
  // this signature takes `acceptedEntries` rather than one entry; and a symlink can be planted
  // between W2 and W5, so W2's result is re-established rather than assumed to still hold.
  const validated = validateEntriesForCapture(profileLike, acceptedEntries, d);
  if (!validated.ok) return validated;

  // Gate 6's hierarchy walk, over BOTH namespaces this call touches: `run/` (the run record it is
  // about to read) and `chapters/`(/<group>) (the chapter record it is about to write). An earlier
  // pass wired this walk only into recovery, so a symlinked `chapters/` or group ancestor was
  // followed transparently on this path (codex DO-NOT-SHIP blocker 3).
  const runHierarchyHazard = inspectRunHierarchyComponents(profileLike, d);
  if (runHierarchyHazard) return { ok: false, halts: [{ halt: 'provenance_hazard', ...runHierarchyHazard }] };
  const chaptersHierarchyHazard = inspectChaptersHierarchyComponents(profileLike, entry, d);
  if (chaptersHierarchyHazard) return { ok: false, halts: [{ halt: 'provenance_hazard', ...chaptersHierarchyHazard }] };

  const groupDirEstablished = establishChapterGroupDir(profileLike, entry, d);
  if (!groupDirEstablished.ok) return { ok: false, halts: [groupDirEstablished.hazard] };

  const runRecord = readRunRecordFromDisk(profileLike, d);
  if (runRecord.kind === 'hazard') return { ok: false, halts: [{ halt: 'provenance_hazard', ...runRecord }] };
  if (runRecord.kind !== 'present') {
    return { recorded: false, reason: 'run_record_unverifiable' };
  }
  if (runRecord.record.run_id !== expectedRunId) {
    return { recorded: false, reason: 'run_id_mismatch' };
  }

  const key = chapterKeyFor(entry);
  const chapterRunData = runRecord.record.chapters[key];
  if (!chapterRunData) {
    return { recorded: false, reason: 'run_record_missing_chapter' };
  }

  let chapterText;
  try {
    chapterText = readFileText(chapterFile, d);
  } catch (err) {
    return { recorded: false, reason: `chapter_read_failed:${err.code ?? err.message}` };
  }

  const assetDir = chapterAssetDir(profileLike, entry);
  let filenames;
  try {
    filenames = listRegularFilesRecursive(assetDir, d);
  } catch (err) {
    return { recorded: false, reason: `asset_listing_failed:${err.code ?? err.message}` };
  }

  const target = profileLike.publish.target;
  // `expectedAssets` defaults to the REAL chapter-paths.mjs extractor — a production caller never
  // injects `deps`, so a missing default here would silently take an inert branch on every real
  // chapter and no record would ever be written on the actual path. The seam stays: a test may
  // still override `deps.expectedAssets` with a stub.
  const extractionFn = deps?.expectedAssets ?? expectedAssets;
  let extraction;
  try {
    extraction = extractionFn(profileLike, entry, chapterFile, chapterText, filenames, target);
  } catch (err) {
    return { recorded: false, reason: `extraction_threw:${err.message}` };
  }
  if (!extraction.ok) {
    return { recorded: false, reason: `extraction_halt:${extraction.halt.construct}@${extraction.halt.line}` };
  }
  if (extraction.assets.length === 0) {
    return { recorded: false, reason: 'zero_in_directory_embeds' };
  }

  // Rule 2 (foreign embed): every asset's absPath must resolve under assetDir. NOTE (not dead
  // code): against the REAL chapter-paths.mjs `expectedAssets`, this branch is unreachable —
  // candidates are only ever built from `assetDir`'s own directory listing, so a foreign
  // destination can never byte-match one and always falls to the extractor's own unmatched-
  // destination halt first. It fires only when `deps.expectedAssets` is a mock that returns an
  // out-of-tree `absPath` (as some unit tests here deliberately do). Kept because the two-variant
  // return shape (`ok`/halt vs. an in-tree assets array) is pinned regardless of which concrete
  // extractor is wired in, and a future extractor need not share this one's guarantee.
  const assetDirResolved = canonicalizeForComparison(assetDir, d);
  if (!assetDirResolved.ok) return { recorded: false, reason: `asset_dir_resolution_failed:${assetDirResolved.reason}` };
  const assetDirCanon = assetDirResolved.segments;
  for (const asset of extraction.assets) {
    const assetResolved = canonicalizeForComparison(asset.absPath, d);
    if (!assetResolved.ok) return { recorded: false, reason: `asset_resolution_failed:${assetResolved.reason}` };
    const assetCanon = assetResolved.segments;
    if (!isSegmentPrefixOf(assetDirCanon, assetCanon) || assetCanon.length === assetDirCanon.length) {
      return { recorded: false, reason: 'foreign_embed' };
    }
  }

  // Rule 3 + 4: every expected key present in `closing`, and closing != opening.
  for (const asset of extraction.assets) {
    if (!Object.hasOwn(chapterRunData.closing, asset.key)) {
      return { recorded: false, reason: 'rule3_missing_from_closing' };
    }
    if (!Object.hasOwn(chapterRunData.opening, asset.key)) {
      // No opening entry (a brand-new file this run) counts as changed for rule 4's purposes.
      continue;
    }
    if (chapterRunData.closing[asset.key] === chapterRunData.opening[asset.key]) {
      return { recorded: false, reason: 'rule4_unchanged' };
    }
  }

  // Rule 5: re-hash every expected image now and require it still differs from `opening`.
  const assetHashes = Object.create(null);
  for (const asset of extraction.assets) {
    const rehash = hashFileNoFollow(asset.absPath, d);
    if (rehash.kind !== 'present') {
      return { recorded: false, reason: `rehash_failed:${rehash.kind}` };
    }
    assetHashes[asset.key] = rehash.digest;
    const openingHash = chapterRunData.opening[asset.key];
    if (openingHash !== undefined && rehash.digest === openingHash) {
      return { recorded: false, reason: 'rule5_reverted_to_opening' };
    }
  }

  const chapterRecord = {
    record_version: 1,
    run_id: expectedRunId,
    build_identity: runRecord.record.build_identity,
    asset_hashes: assetHashes,
  };
  const recordText = JSON.stringify(chapterRecord, null, 2);

  const finalPath = chapterRecordPath(profileLike, entry);
  const tempPath = `${finalPath}.${d.randomUUID()}.tmp`;
  let fd;
  try {
    fd = d.openSync(tempPath, fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_WRONLY | fs.constants.O_NOFOLLOW, 0o644);
    writeFull(fd, Buffer.from(recordText, 'utf8'), d);
  } catch (err) {
    if (fd !== undefined) {
      // Best-effort: this closeSync failing must never MASK the write failure we are about to
      // report (codex round 3).
      closeBestEffort(fd, d);
      unlinkBestEffort(tempPath, d);
    }
    return { ok: false, halts: [{ halt: 'provenance_hazard', reason: err.reason ?? 'write_failed', path: tempPath, detail: err.message }] };
  }
  try {
    d.closeSync(fd);
  } catch (err) {
    // Nothing durable has been committed yet at this point (the rename below hasn't run) — an
    // ordinary halt, with best-effort cleanup of the still-fully-written-but-unclosed temp.
    unlinkBestEffort(tempPath, d);
    return { ok: false, halts: [{ halt: 'provenance_hazard', reason: 'close_failed', path: tempPath, detail: err.message }] };
  }
  try {
    d.renameSync(tempPath, finalPath);
  } catch (err) {
    unlinkBestEffort(tempPath, d);
    return { ok: false, halts: [{ halt: 'provenance_hazard', reason: 'rename_failed', path: finalPath, detail: err.message }] };
  }

  return { recorded: true, reason: null };
}

function readFileText(path, deps) {
  const read = readLeafText(path, deps);
  if (read.kind === 'present') return read.text;
  throw new Error(`cannot read ${path}: ${read.reason ?? read.kind}`);
}

function listRegularFilesRecursive(assetDir, deps) {
  const out = [];
  walkRegularFiles(assetDir, deps, (_absPath, relPath) => out.push(relPath));
  return out;
}

// ---------------------------------------------------------------------------------------------
// Row 5 — buildProvenanceReport. Reads CHAPTER records only (never the run record — the run
// record belongs to one run and may be absent, older, or from a different machine by audit time).
// ---------------------------------------------------------------------------------------------

/**
 * Build the W6 audit report: one row per manifest entry, in manifest order, classifying the delta
 * between each chapter's recorded identity and the CURRENT one. On a skipped profile, returns
 * `provenance_unavailable` rows with zero UI requests and zero record reads.
 *
 * @param {object} profileLike
 * @param {Array<object>} entries
 * @param {import('./build-identity.mjs').UiReadObservation|null} [currentObservation]
 * @param {object} [deps]
 * @returns {{rows: Array<object>}|{needs_ui_read: true, region_hint: string}|{ok: false, halts: Array<object>}}
 */
export function buildProvenanceReport(profileLike, entries, currentObservation, deps) {
  const d = mergeDeps(deps);

  const ownership = assertProvenanceOwnership(profileLike, d);
  if (ownership.skip) {
    return {
      rows: entries.map((entry) => ({
        key: chapterKeyFor(entry),
        value: 'unknown',
        source: null,
        resolution_reason: null,
        classification: 'indeterminate',
        classification_reason: 'provenance_unavailable',
      })),
    };
  }
  if (!ownership.ok) return { ok: false, halts: ownership.halts };

  // W6 is independently callable (the audit mode for already-merged chapters), so it routinely
  // runs against a manifest W2 never validated in that session — it must run gates 1-4 itself
  // before deriving a single path, or it reads an unrelated record from outside the intended tree
  // and reports its contents under the manifest entry's name.
  const validated = validateEntriesForCapture(profileLike, entries, d);
  if (!validated.ok) return validated;

  const buildIdentity = profileLike.capture.build_identity ?? null;
  const uiReadEnabled = buildIdentity?.ui_read !== false;
  let commandOutcome = null;
  if (buildIdentity?.command) {
    commandOutcome = d.runIdentityCommand(buildIdentity.command);
  }
  const current = resolveBuildIdentity({ commandOutcome, uiReadEnabled, uiObservation: currentObservation });
  if (current.needs_ui_read) return current;

  const rows = [];
  for (const entry of entries) {
    const key = chapterKeyFor(entry);
    // Gate 6's hierarchy walk over `chapters/`(/<group>) for THIS entry, before its record is read
    // — an earlier pass wired this walk only into recovery, so a symlinked `chapters/` or group
    // ancestor was followed transparently on the W6 read path (codex DO-NOT-SHIP blocker 3).
    const chaptersHierarchyHazard = inspectChaptersHierarchyComponents(profileLike, entry, d);
    if (chaptersHierarchyHazard) return { ok: false, halts: [{ halt: 'provenance_hazard', ...chaptersHierarchyHazard }] };
    const recordRead = readChapterRecordFromDisk(profileLike, entry, d);
    if (recordRead.kind === 'hazard') return { ok: false, halts: [{ halt: 'provenance_hazard', ...recordRead }] };

    // W6 must verify the chapter's OWN embedded images, never the whole asset directory — hashing
    // every regular file under chapterAssetDir (an earlier version of this loop) lets an unrelated
    // leftover file masquerade as staleness, and lets a chapter with ZERO real embeds but stale
    // leftover images appear "verified" (codex DO-NOT-SHIP blocker 4). So the real extractor runs
    // for EVERY entry, unconditionally — an extraction halt anywhere in the manifest halts this
    // whole report rather than silently reporting partial rows, matching W5's own halt discipline.
    const chapterFile = posixJoin(profileLike.publish.chapters_dir, chapterRelPath(entry));
    let chapterText;
    try {
      chapterText = readFileText(chapterFile, d);
    } catch (err) {
      return { ok: false, halts: [{ halt: 'chapter_read_failed', message: `cannot read chapter '${chapterFile}': ${err.code ?? err.message}`, key }] };
    }
    const assetDir = chapterAssetDir(profileLike, entry);
    let filenames;
    try {
      filenames = listRegularFilesRecursive(assetDir, d);
    } catch (err) {
      return { ok: false, halts: [{ halt: 'asset_listing_failed', message: `cannot list the asset directory for '${key}': ${err.code ?? err.message}`, key }] };
    }
    const extractionFn = deps?.expectedAssets ?? expectedAssets;
    let extraction;
    try {
      extraction = extractionFn(profileLike, entry, chapterFile, chapterText, filenames, profileLike.publish.target);
    } catch (err) {
      return { ok: false, halts: [{ halt: 'extraction_threw', message: err.message, key }] };
    }
    if (!extraction.ok) {
      return { ok: false, halts: [{ halt: 'extraction_halt', construct: extraction.halt.construct, line: extraction.halt.line, key }] };
    }

    let recordState;
    let chapterRecord = null;
    if (recordRead.kind === 'absent') {
      recordState = 'absent';
    } else if (recordRead.kind === 'invalid') {
      recordState = 'malformed';
    } else {
      chapterRecord = recordRead.record;
      if (chapterRecord.record_version !== 1) {
        recordState = 'unsupported_version';
      } else {
        const currentHashes = Object.create(null);
        for (const asset of extraction.assets) {
          const hashed = hashFileNoFollow(asset.absPath, d);
          if (hashed.kind === 'present') currentHashes[asset.key] = hashed.digest;
        }
        const verify = verifyRecord(chapterRecord.asset_hashes, currentHashes);
        recordState = verify.status === 'ok' ? 'ok' : 'stale';
      }
    }

    // classifyBuildDelta's `record` parameter is a BuildIdentity value ({value, source,
    // resolution_reason, detail}) — the chapter record's OWN `build_identity` sub-object, never the
    // chapter record itself, which is one level up and has no `.value`/`.source` of its own.
    const recordIdentity = chapterRecord?.build_identity ?? null;
    const delta = classifyBuildDelta({ current, recordState, record: recordIdentity });
    rows.push({
      key,
      value: formatIdentityValue(recordIdentity?.value ?? null),
      source: delta.recorded_source,
      resolution_reason: recordIdentity?.resolution_reason ?? null,
      classification: delta.classification,
      classification_reason: delta.classification_reason,
      current_source: delta.current_source,
    });
  }

  return { rows };
}

// ---------------------------------------------------------------------------------------------
// Row 6 — the nine-state classifier and its two repairs. Precedence, evaluated top to bottom:
// not_active -> orphan_temp -> absent -> partial -> malformed -> prepared -> open -> committed ->
// divergent. See row6-generated.md for the generated, byte-compared authority this mirrors.
// ---------------------------------------------------------------------------------------------

const STATE_ONLY_EXPECTED = new Set(['not_active', 'orphan_temp', 'absent', 'partial', 'malformed', 'divergent']);

const PROGRESS_CHAINS = {
  orphan_temp: ['orphan_temp', 'absent'],
  partial: ['partial', 'absent'],
  prepared: ['prepared', 'open', 'absent'],
  open: ['open', 'absent'],
  committed: ['committed', 'absent'],
};

const REPAIR_FOR_STATE = {
  orphan_temp: 'abortCaptureRun',
  partial: 'abortCaptureRun',
  prepared: 'abortCaptureRun',
  open: 'abortCaptureRun',
  committed: 'cleanupCommittedRun',
};

// O_NOFOLLOW on a leaf open only refuses a symlink at the FINAL path component — an ancestor
// directory that is itself a symlink is followed transparently by the kernel regardless of that
// flag. So every hierarchy component a leaf lives under is walked and lstat-checked SEPARATELY,
// before any leaf is ever opened. Shared by both namespaces this module owns: `run/` (token,
// record, temps) and `chapters/`(/<group>) (chapter records) — every consumer that reads or
// writes a leaf under either namespace calls the matching hierarchy check first, not only row 6's
// recovery path (an earlier pass wired this into recovery alone, which is exactly why a symlinked
// `chapters/` or group ancestor was followed on the W5/W6 paths — codex DO-NOT-SHIP blocker 3).
function inspectHierarchyChain(dirs, deps) {
  for (const dir of dirs) {
    const inspected = inspectDirComponent(dir, deps);
    if (inspected.kind === 'hazard') return inspected;
    // 'absent' is expected on a first run before establishment — a leaf beneath an absent
    // ancestor will itself read as absent, which is the correct classification.
  }
  return null;
}

function inspectRunHierarchyComponents(profileLike, deps) {
  return inspectHierarchyChain([provenanceRoot(profileLike), runNamespaceDir(profileLike)], deps);
}

// The `chapters/` namespace, plus the entry's own group directory when grouped — the ancestor
// chain a chapter record's leaf actually lives under.
function inspectChaptersHierarchyComponents(profileLike, entry, deps) {
  const dirs = [provenanceRoot(profileLike), chaptersNamespaceDir(profileLike)];
  if (entry.group !== undefined) dirs.push(posixJoin(chaptersNamespaceDir(profileLike), String(entry.group)));
  return inspectHierarchyChain(dirs, deps);
}

function inspectTokenAndRecordAndTemps(profileLike, deps) {
  const hierarchyHazard = inspectRunHierarchyComponents(profileLike, deps);
  if (hierarchyHazard) return { hazard: hierarchyHazard };

  // Both leaves are read through the SAME gate-6 helpers every other reader in this module uses
  // (`readLeafText` / `readRunRecordFromDisk`: O_NOFOLLOW open, fstat on that same descriptor,
  // regular file, nlink === 1) rather than through a second hand-rolled open/read/close of their
  // own — one definition of "how a leaf is read here" is what keeps the classifier's view of disk
  // identical to the pipeline's.
  const tokenPath = pendingTokenPath(profileLike);
  const tokenRead = readLeafText(tokenPath, deps);
  if (tokenRead.kind === 'hazard') return { hazard: tokenRead };
  let tokenState = 'absent';
  let tokenValue = null;
  if (tokenRead.kind === 'present') {
    const parsed = parseJsonStrict(tokenRead.text);
    // `parsed.value` is optional-chained: a token body of the literal `null` parses SUCCESSFULLY
    // (`{ok: true, value: null}`), and it is the one JSON value that is both non-object and
    // dereferenceable-looking. Bodies `5`, `"str"` and `[]` all reach `typeof …run_id` harmlessly
    // and classify as invalid; `null` threw a TypeError, breaking this function's documented
    // totality over (token, record, temps). Measured before the fix.
    if (parsed.ok && typeof parsed.value?.run_id === 'string' && isValidDigest(parsed.value?.opening_digest)) {
      tokenState = 'valid';
      tokenValue = parsed.value;
    } else {
      tokenState = 'invalid';
    }
  }

  const recordRead = readRunRecordFromDisk(profileLike, deps);
  if (recordRead.kind === 'hazard') return { hazard: recordRead };
  let recordState = 'absent';
  let recordValue = null;
  if (recordRead.kind === 'invalid') {
    recordState = 'invalid';
  } else if (recordRead.kind === 'present') {
    recordState = 'valid';
    recordValue = recordRead.record;
  }

  // Recovery is read-only (it commits nothing), so a listing hazard here is an ordinary hazard —
  // propagated through the same `{hazard}` discriminator this function already uses for the
  // token/record reads above, which its own callers (`recoverProvenanceState`, `repair`) already
  // dispatch on.
  const tempsListed = listMatchingTemps(profileLike, deps);
  if (!tempsListed.ok) return { hazard: tempsListed.hazard };
  const temps = tempsListed.temps;
  for (const temp of temps) {
    const tempLeaf = openLeafNoFollow(temp, fs.constants.O_RDONLY, deps);
    if (tempLeaf.kind === 'hazard') return { hazard: tempLeaf };
    if (tempLeaf.kind === 'present') closeBestEffort(tempLeaf.fd, deps);
  }

  return {
    tokenState,
    tokenValue,
    recordState,
    recordValue,
    hasTemps: temps.length > 0,
    temps,
  };
}

function classify(observed) {
  const { tokenState, tokenValue, recordState, recordValue, hasTemps } = observed;

  if (tokenState === 'absent') {
    return hasTemps ? { state: 'orphan_temp' } : { state: 'absent' };
  }
  if (tokenState === 'invalid') {
    return { state: 'partial' };
  }
  if (recordState === 'invalid') {
    return { state: 'malformed' };
  }
  if (recordState === 'absent') {
    return hasTemps ? { state: 'prepared' } : { state: 'open' };
  }
  // recordState === 'valid'
  if (tokenValue.run_id !== recordValue.run_id) {
    return hasTemps ? { state: 'prepared' } : { state: 'open' };
  }
  if (tokenValue.opening_digest === recordValue.opening_digest) {
    return { state: 'committed' };
  }
  return { state: 'divergent' };
}

function expectedForState(state, tokenValue) {
  if (STATE_ONLY_EXPECTED.has(state)) {
    return { state, run_id: null, opening_digest: null };
  }
  return { state, run_id: tokenValue.run_id, opening_digest: tokenValue.opening_digest };
}

/**
 * Classify this profile's row-6 state — a TOTAL function of (token, record, temps) observed AFTER
 * gate 6. Mutates nothing. On a skipped profile (this run's own W1 outcome), returns `not_active`
 * with zero token/record/temp reads.
 *
 * @param {object} profileLike
 * @param {object} [deps]
 * @returns {{state: string, action: string|null, expected: object, files: string[]}|{ok: false, halts: Array<object>}}
 */
export function recoverProvenanceState(profileLike, deps) {
  const d = mergeDeps(deps);
  const ownership = assertProvenanceOwnership(profileLike, d);
  if (ownership.skip) {
    return { state: 'not_active', action: null, expected: { state: 'not_active', run_id: null, opening_digest: null }, files: [] };
  }
  if (!ownership.ok) return { ok: false, halts: [{ halt: 'ownership_halt', halts: ownership.halts }] };

  const observed = inspectTokenAndRecordAndTemps(profileLike, d);
  if (observed.hazard) return { ok: false, halts: [{ halt: 'provenance_hazard', ...observed.hazard }] };

  const { state } = classify(observed);
  const action = REPAIR_FOR_STATE[state] ?? null;
  const expected = expectedForState(state, observed.tokenValue);
  const files = [pendingTokenPath(profileLike), runRecordPath(profileLike), ...observed.temps];
  return { state, action, expected, files };
}

// Both repairs share ONE mutation order — every matching temp first, the token last — so the order
// is a property of this function rather than a parameter its two callers could disagree about.
// They differ only in `calledApi`, which is what the wrong-executor check below is keyed on.
function repair(profileLike, expected, deps, calledApi) {
  const d = mergeDeps(deps);
  const ownership = assertProvenanceOwnership(profileLike, d);
  if (ownership.skip) return { ok: true, skipped: true, removed: [] };
  if (!ownership.ok) return { ok: false, halts: [{ halt: 'ownership_halt', halts: ownership.halts }] };

  const observed = inspectTokenAndRecordAndTemps(profileLike, d);
  if (observed.hazard) return { ok: false, halts: [{ halt: 'provenance_hazard', ...observed.hazard }] };
  const { state: observedState } = classify(observed);

  // The wrong-executor check is keyed on `expected.state` — the state the CALLER is claiming to
  // repair — never on `observedState`, the state currently on disk. Keying it on `observedState`
  // is a real bug, not a style choice: `REPAIR_FOR_STATE` has no entry for the four states with no
  // prescribed repair (not_active, absent, malformed, divergent), so the moment the tree has
  // ALREADY reached one of those (e.g. a concurrent abort already finished), the check silently
  // stops comparing anything — a caller invoking `cleanupCommittedRun` against an `expected.state`
  // of `open` (whose prescribed repair is `abortCaptureRun`) is accepted as a no-op the instant the
  // tree happens to have reached `absent` first, which is exactly the wrong executor for the state
  // it claims to be resolving (codex, important #5). Keying on `expected.state` instead means the
  // check is a property of the REQUEST, not of a filesystem race the caller cannot control.
  const prescribed = REPAIR_FOR_STATE[expected.state];
  if (prescribed !== undefined && prescribed !== calledApi) {
    return { ok: false, halts: [{ halt: 'stale_verdict', reason: 'wrong_repair_for_state', observedState, expectedState: expected.state, calledApi }] };
  }

  const chain = PROGRESS_CHAINS[expected.state];
  if (chain === undefined || !chain.includes(observedState)) {
    return { ok: false, halts: [{ halt: 'stale_verdict', reason: 'off_progress_chain', observedState, expectedState: expected.state }] };
  }

  // Fingerprint check where the expected state carries one (skip for state-only-expected values,
  // which carry {run_id:null, opening_digest:null} by contract).
  if (!STATE_ONLY_EXPECTED.has(expected.state)) {
    const currentFingerprint = observed.tokenState === 'valid' ? observed.tokenValue : null;
    if (currentFingerprint === null || currentFingerprint.run_id !== expected.run_id || currentFingerprint.opening_digest !== expected.opening_digest) {
      // The token is gone or changed identity — but if we've already reached (or passed) the
      // final post_state for this chain, that is idempotent success, not staleness.
      const finalState = chain[chain.length - 1];
      if (observedState === finalState) {
        return { ok: true, removed: [], noop: true };
      }
      return { ok: false, halts: [{ halt: 'stale_verdict', reason: 'fingerprint_changed', observedState }] };
    }
  }

  const finalState = chain[chain.length - 1];
  if (observedState === finalState) {
    return { ok: true, removed: [], noop: true };
  }

  // A mutation_failed halt names the path, what was already removed, AND the state the tree is
  // now in (re-observed fresh rather than reasoned about in-memory, since removing SOME but not
  // all temps can either leave the classification unchanged or flip it, depending on whether the
  // failure landed on the last one) — so the operator re-runs rather than guesses (codex,
  // important #5: an earlier version omitted this).
  function mutationFailedHalt(path, removedSoFar, err) {
    const reobserved = inspectTokenAndRecordAndTemps(profileLike, d);
    const currentState = reobserved.hazard ? null : classify(reobserved).state;
    return { ok: false, halts: [{ halt: 'mutation_failed', path, removed: removedSoFar, detail: err.message, currentState }] };
  }

  // Resume the remaining suffix of the mutation order from wherever we are.
  const removed = [];
  for (const temp of observed.temps) {
    try {
      d.unlinkSync(temp);
      removed.push(temp);
    } catch (err) {
      return mutationFailedHalt(temp, removed, err);
    }
  }
  // The token is deleted UNCONDITIONALLY here, never gated on `observedState` — both repairs'
  // whole job, once every temp is gone, is deleting the token: for abort, that is what takes
  // 'open' to 'absent'; for cleanup, temps never blocked 'committed' in the first place, so this
  // is the only remaining step. Confirmed intentional (team-lead review, #362) — an earlier draft
  // had a conditional here whose body was empty comments only, which is exactly what a LOST edit
  // looks like; there was no lost edit, the condition was simply never needed.
  const tokenPath = pendingTokenPath(profileLike);
  try {
    d.unlinkSync(tokenPath);
    removed.push(tokenPath);
  } catch (err) {
    if (err.code !== 'ENOENT') {
      return mutationFailedHalt(tokenPath, removed, err);
    }
  }

  return { ok: true, removed };
}

/**
 * Repair the `orphan_temp` / `partial` / `prepared` / `open` states: remove every matching temp
 * first, the token last. Idempotent — running it twice, or on an already-absent token, succeeds
 * with `{ok:true, removed:[], noop:true}`.
 *
 * @param {object} profileLike
 * @param {{state: string, run_id: string|null, opening_digest: string|null}} expected
 * @param {object} [deps]
 * @returns {{ok: true, removed: string[], noop?: true}|{ok: true, skipped: true, removed: []}|{ok: false, halts: Array<object>}}
 */
export function abortCaptureRun(profileLike, expected, deps) {
  return repair(profileLike, expected, deps, 'abortCaptureRun');
}

/**
 * Repair the `committed` state: remove every matching temp first, the token last, ONLY while the
 * token and record still show `committed` at the expected fingerprint. Idempotent.
 *
 * @param {object} profileLike
 * @param {{state: string, run_id: string|null, opening_digest: string|null}} expected
 * @param {object} [deps]
 * @returns {{ok: true, removed: string[], noop?: true}|{ok: true, skipped: true, removed: []}|{ok: false, halts: Array<object>}}
 */
export function cleanupCommittedRun(profileLike, expected, deps) {
  return repair(profileLike, expected, deps, 'cleanupCommittedRun');
}
