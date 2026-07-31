// enduser-handbook asset — non-normative reference implementation of the build-provenance disk
// layer. The normative contract lives in SKILL.md (W1's ownership gate, W2's open/close sequence,
// W5's completeness rule + chapter record, W6's report) and in references/capture-engines.md,
// references/capture-safety.md and references/revalidation.md. [round 13] This header used to say
// the row-6 surfaces were generated from a companion document's authority. That document has never
// existed in this repository, on any branch — it was a planning artifact that stayed outside it, so
// the citation sent a reader after a file they could not open. The declaration beside this one was
// corrected in round 11; this copy was not, because the gate that catches dangling citations scanned
// only `*.d.mts`. SKILL.md is the authority for the row-6 states, and it ships.
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
// `provenanceRoot` and `chapterRecordPath`, plus `sweepChapterProvenanceTemps` (codex round 5,
// finding 3): an ELEVENTH, operator-invoked export that is deliberately NOT a member of row 6's
// recovery trio — it answers a different question (is there a leftover chapter-record temp?) over
// a domain row 6 does not cover (`chapters/`, not `run/`), so it is its own single-purpose sweep
// rather than a fourth state the classifier's `(token, record, temps)` tuple would have to absorb;
// see the comment above its definition. Eleven named exports; the eight entrypoints are a
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
  describeBuildIdentityWarning,
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

// Every catch in this module reads a caught value's `.message`/`.code`/`.reason` to build a halt
// message or drive a control-flow decision (an errno comparison) — an assumption that the thrown
// value is Error-shaped. JavaScript permits throwing ANY value (`throw null`, `throw 'oops'`,
// `throw 42`), and every `deps.*` call in this file is a deliberately injectable seam (an
// operator-supplied identity command, `randomUUID`, or any fs function a caller/test overrides), so
// a caught value here is never guaranteed to be an object at all, let alone an Error. Every
// property read off a caught `err` anywhere in this file goes through one of these three now,
// never a raw `err.<name>` access or an unguarded `String(err)`.
//
// `null`/`undefined` are the ONLY two values where a bracket property read (`err[name]`) is
// GUARANTEED to throw — every other value, INCLUDING a thrown function (`typeof === 'function'`,
// which the previous version of this helper excluded by checking `typeof err === 'object'` —
// round 11, finding 1c: a thrown function carrying its own `.code` silently lost that field,
// changing `openCaptureRun`'s `EEXIST` classification to the generic `provenance_hazard`) or a
// primitive (autoboxed safely) — supports property access with no risk AS A RULE. But "as a rule"
// is not "always": a thrown Proxy (or a plain object with a throwing GETTER for this exact name)
// can make `err[name]` itself throw regardless of `err`'s type (round 12, finding 1) — reproduced
// by codex through `openCaptureRun`'s identity-command guard, seam trace `["open"]` only: neither
// `close` nor `unlink` ran, the reservation leaking a FOURTH distinct way. The try/catch below is
// what makes this total against THAT, not just against null/undefined.
function errProp(err, name) {
  if (err === null || err === undefined) return undefined;
  try {
    return err[name];
  } catch {
    return undefined;
  }
}

// The message-building fallback — ALWAYS returns a string, for any thrown value whatsoever.
// `err.message` (read via `errProp`) wins ONLY when it is ALREADY a string (round 11, finding 1b: a
// thrown `{message: Symbol(...)}` made the previous `?? ` chain hand back the Symbol itself, which
// then threw on template-literal interpolation). Otherwise falls to `String(err)` — but `String()`
// is not total either (round 11, finding 1a: `Object.create(null)` has no `toString`/
// `Symbol.toPrimitive` at all, so `String()` throws `Cannot convert object to primitive value`) —
// so a fallback, `Object.prototype.toString.call(err)`, which the language specifies to read
// `err[Symbol.toStringTag]` and never throw for an ORDINARY object. "Ordinary" is doing real work
// in that sentence: a Proxy whose `get` trap throws for EVERY property (round 12, finding 1) makes
// even THIS throw, since the toStringTag lookup is itself a property read on `err`. The final
// literal string below is the one thing in this chain that cannot possibly throw — no property
// read, no coercion, nothing but a string constant — so it is where the chain has to end. Every
// branch returns a string; none can throw, for any input, however hostile.
function describeThrown(err) {
  const message = errProp(err, 'message');
  if (typeof message === 'string') return message;
  try {
    return String(err);
  } catch {
    try {
      return Object.prototype.toString.call(err);
    } catch {
      return '<unstringifiable thrown value>';
    }
  }
}

// Prefer the first NAMED field that is itself a string (`.code`, `.reason`, ...), else
// `describeThrown(err)` — the same non-string-property risk `describeThrown` guards for
// `.message` applies symmetrically to every other field this module reads off a caught value: a
// thrown `{code: Symbol(...)}` would otherwise hand a Symbol straight into a template literal.
// Every message-building catch in this file that used to chain `errProp(err, 'x') ??
// errProp(err, 'y') ?? describeThrown(err)` goes through this instead.
function describeThrownField(err, ...names) {
  for (const name of names) {
    const value = errProp(err, name);
    if (typeof value === 'string') return value;
  }
  return describeThrown(err);
}

// ---------------------------------------------------------------------------------------------
// The filesystem seam. Every exported function THAT TOUCHES DISK accepts `deps` and defaults to
// this object; the pure ones (`jcsCanonicalize`, `sha256HexOfCanonical`, `digestOpeningPayload`,
// `provenanceRoot`, `chapterRecordPath`, `readRunRecordText`, `readChapterRecordText`) take no
// `deps` at all. [round 14] The unqualified "every exported function" was itself false, and it was
// written in round 13 while correcting a DIFFERENT false universal in this same sentence — the
// replacement inherited the shape of what it replaced. The set is pinned in
// tests/skill-call-signatures.test.mjs rather than restated here. [round
// 13] This comment used to say `deps` comes LAST, which is false for `openCaptureRun`,
// `closeCaptureRun` and `buildProvenanceReport`: each takes `identityCommandOutcome` AFTER it. That
// is the very wrong-slot trap round 12 fixed in SKILL.md, still spelled out here as a rule — a
// caller trusting this line reintroduces it. Read the signature, not this comment, for position; no
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
      return { ok: false, detail: `${command}: ${describeThrown(err)}` };
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
    if (errProp(err, 'code') === 'ENOENT') return { kind: 'absent' };
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
    if (errProp(err, 'code') !== 'EEXIST') {
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
    if (errProp(err, 'code') === 'ENOENT') return { kind: 'absent' };
    if (errProp(err, 'code') === 'ELOOP') return { kind: 'hazard', reason: 'symlink', path: absPath };
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

// [round 17] The word an operator can act on, for a leaf inspection that is not `present`. EVERY
// unreadable leaf comes back with kind `hazard` — `hard_link`, `non_regular`, `symlink` and
// `inspection_failure` are all carried in `reason` — so interpolating `.kind` into an
// operator-facing string collapses four distinct situations, calling for four distinct actions,
// into the single word `hazard`. Three sites did exactly that (the opening/closing snapshot, W5's
// publish-time rehash, W6's current-hash pass) while a fourth, `readFileText`, already spelled the
// `reason ?? kind` fallback inline and so was right by accident of being written later. It is a
// function now, so the next site to report an unreadable leaf inherits the correct form rather than
// re-deriving it. `absent` has no reason and is its own answer, which is what the fallback is for.
function unreadableWord(inspection) {
  return inspection.reason ?? inspection.kind;
}

/**
 * Every word an asset-tree hazard can be reported under. The walk contributes `symlink` and
 * `non_regular`; the leaf inspection contributes those two plus `hard_link` and
 * `inspection_failure`. Pinned against the real producers by test rather than by inspection, since
 * a word missing from here would make legitimate records unreadable — the reader below rejects a
 * record carrying anything else, and that rejection refuses every chapter in the run.
 */
const ASSET_HAZARD_REASONS = new Set(['symlink', 'non_regular', 'hard_link', 'inspection_failure']);

/**
 * A persisted hazard member, `<assetDirRelativePath>:<reason>`. The path may name a DIRECTORY (a
 * refused directory withholds everything beneath it), so it is validated as a relative path rather
 * than as an asset key — requiring a file-shaped key here would reject the very case round 17
 * added. `..` is refused because a hazard is a statement about something inside the asset tree, and
 * a member that walks out of it describes nothing this run observed.
 */
function isWellFormedHazard(member) {
  if (typeof member !== 'string') return false;
  const at = member.lastIndexOf(':');
  if (at <= 0) return false; // no colon at all, or an empty path
  if (!ASSET_HAZARD_REASONS.has(member.slice(at + 1))) return false;
  const segments = member.slice(0, at).split('/');
  return segments.every((s) => s !== '' && s !== '.' && s !== '..');
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

// The directory a chapter's own record (and, transiently, its write temp) lives in —
// `<root>/chapters/<group>` (grouped) or `<root>/chapters` (flat). Split out of `chapterRecordPath`
// below so `sweepChapterProvenanceTemps` (codex round 5, finding 3) can list a chapter's temp
// directory without re-deriving the group/flat branch a third time; `chapterRecordPath` and
// `sweepChapterProvenanceTemps` both call this one function rather than keeping their own copies
// that could disagree, same rationale as `chapterRecordPath`'s own docstring below.
function chapterRecordDir(profileLike, entry) {
  const base = chaptersNamespaceDir(profileLike);
  // `entry.group !== undefined`, never a truthy check — chapter-paths.mjs's own convention
  // (chapterRelPath, outputDirTail), documented there as "a falsy-but-present group value must
  // never silently derive a flat path". A truthy check would treat `group: 0` (or `''`) as flat
  // here while chapterAssetDir treats the identical entry as grouped — a real cross-module
  // classification mismatch for a malformed-but-present manifest value (found by paths, #362).
  return entry.group !== undefined ? posixJoin(base, String(entry.group)) : base;
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
  const fileName = `${String(entry.slug)}.json`;
  return posixJoin(chapterRecordDir(profileLike, entry), fileName);
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
      const code = errProp(err, 'code');
      if (code === 'ENOENT' || code === 'ENOTDIR') {
        if (idx === 0) return { ok: true, segments: tail }; // nothing at all resolves; degrade gracefully
        tail = [segments[idx - 1], ...tail];
        idx -= 1;
        continue;
      }
      // ELOOP (a symlink cycle) or any other inspection failure — a hazard, not a value.
      return { ok: false, reason: code === 'ELOOP' ? 'symlink_cycle' : 'inspection_failure', path: candidate };
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
    if (errProp(err, 'code') !== 'EEXIST') {
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

// The longest EXISTING ancestor of `pathSegs`, walked DOWNWARD starting at `rootSegs` — never
// above it. Gate 3's own root canonicalization already fixed the coordinate system the caller
// checks containment in; if the canonical root itself is not physically present either, nothing
// under it can hold a planted symlink, so that degrades to "no ancestor to check" rather than
// climbing past the root looking for one that predates it. Existence is probed with
// `deps.lstatSync` only — never `realpath` — matching gate 3's own no-follow walk: this function
// only decides WHERE the walk should start, `resolvePhysicalContainment` still performs the actual
// symlink-substitution walk over whatever prefix comes back.
//
// Also returns `tailSegs` — the not-yet-existing segments beyond the ancestor (empty when
// `exists: false`, since then nothing at all resolves and there is no ancestor to hang a tail off).
// These segments cannot themselves contain a symlink (nothing along them exists yet, and a symlink
// is itself a filesystem entry that would have to exist to be one), so the CALLER may append them,
// unresolved, straight onto the ancestor's own resolved physical path to get the composite physical
// identity of the not-yet-created directory — see the caller (round 6, finding 1) for why that
// composite, not the bare ancestor, is what gate 4 must compare.
//
// @param {string[]} rootSegs  the canonical output root's own segments (never re-walked here)
// @param {string[]} pathSegs  the candidate's full segments — MUST start with `rootSegs`
// @param {object} deps
// @returns {{ok: true, exists: false, tailSegs: []}|{ok: true, exists: true, path: string, tailSegs: string[]}|{ok: false, error: Error}}
function longestExistingAncestor(rootSegs, pathSegs, deps) {
  let lastExistingIdx = -1;
  for (let idx = rootSegs.length; idx <= pathSegs.length; idx++) {
    const candidate = `/${pathSegs.slice(0, idx).join('/')}`;
    try {
      deps.lstatSync(candidate);
    } catch (err) {
      if (errProp(err, 'code') === 'ENOENT' || errProp(err, 'code') === 'ENOTDIR') break;
      return { ok: false, error: err };
    }
    lastExistingIdx = idx;
  }
  if (lastExistingIdx < 0) return { ok: true, exists: false, tailSegs: [] };
  return {
    ok: true,
    exists: true,
    path: `/${pathSegs.slice(0, lastExistingIdx).join('/')}`,
    tailSegs: pathSegs.slice(lastExistingIdx),
  };
}

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
    // therefore checked HERE, at this call site, before gate 3 ever runs on the full path — matching
    // this module's own established rule elsewhere (gate 6's `ensureDirComponent`/
    // `inspectDirComponent`) that ENOENT-on-a-not-yet-established path is expected, not a hazard,
    // while any OTHER lstat failure still is.
    let assetDirExists = true;
    try {
      deps.lstatSync(assetDir);
    } catch (err) {
      const code = errProp(err, 'code');
      if (code === 'ENOENT' || code === 'ENOTDIR') {
        assetDirExists = false;
      } else {
        return haltResult('provenance_hazard', `cannot inspect asset directory '${assetDir}': ${typeof code === 'string' ? code : describeThrown(err)}`, { assetDir });
      }
    }

    // A missing LEAF is fine — but skipping gate 3 ENTIRELY throws away the containment check over
    // the whole path, not just the missing tail: a symlinked ANCESTOR (`/safe/assets/admin` ->
    // `/outside`, with `/outside/items` not yet created) still lstats the full candidate as ENOENT,
    // and the capture command that runs afterwards then writes `/outside/items` into existence —
    // outside `capture.output_dir` — with nothing here ever having checked the ancestor that made it
    // possible. Run the SAME component-wise walk over the longest EXISTING ancestor prefix instead;
    // the one thing the leaf-missing case legitimately cannot supply is the leaf itself, not the
    // ancestors above it.
    let containmentTarget = assetDir;
    let missingTailSegs = [];
    if (!assetDirExists) {
      const ancestor = longestExistingAncestor(outputRootResolved.segments, rawSegments(assetDir), deps);
      if (!ancestor.ok) {
        return haltResult(
          'provenance_hazard',
          `cannot inspect an ancestor of asset directory '${assetDir}': ${describeThrownField(ancestor.error, 'code')}`,
          { assetDir },
        );
      }
      // Nothing at all exists yet along this path (not even the canonical root itself) — nothing to
      // check, and nothing to add to gate 4's cross-entry collision set. This is still safe with no
      // symlink in play anywhere on the path (nothing exists to BE one), gate 2's lexical-uniqueness
      // check already guarantees physical uniqueness too in that case.
      if (!ancestor.exists) continue;
      containmentTarget = ancestor.path;
      missingTailSegs = ancestor.tailSegs;
    }

    const result = resolvePhysicalContainment(canonicalOutputRoot, containmentTarget, containmentDeps);
    if (!result.ok) {
      if (result.halt.reason === 'inspection-failed') {
        return haltResult('provenance_hazard', result.halt.detail, { assetDir });
      }
      if (result.halt.reason === 'cycle') {
        return haltResult('symlink_cycle', result.halt.detail, { assetDir });
      }
      return haltResult('asset_dir_escapes_output_dir', result.halt.detail, { assetDir, slug: entry.slug });
    }
    // A not-yet-created leaf's physical identity for gate 4 is the resolved (symlink-substituted)
    // EXISTING ancestor plus its still-missing tail, UNCHANGED — never the bare ancestor alone
    // (round 6, finding 1: two different chapters resolving through two different symlinked group
    // ancestors into the SAME not-yet-created leaf directory manufactured no collision at all under
    // the bare-ancestor comparison, and the capture command run afterwards silently overwrote one
    // chapter's assets with the other's). The tail cannot itself hide a further symlink — nothing
    // along it exists yet, and a symlink is itself a filesystem entry that would have to exist to be
    // one — so appending it unresolved is exact, not an approximation. This still keeps two
    // chapters that only share an ANCESTOR (different tails) from colliding: their composites differ
    // even though `result.resolved` alone is identical for both (see the round-5 sibling test above).
    // `missingTailSegs` is empty here whenever `assetDirExists` is true (never reassigned off its
    // `[]` initializer on that branch) and non-empty whenever it is false and reached this line
    // (the `!ancestor.exists` case above already `continue`d, and `assetDir`'s own already-checked
    // ENOENT rules out `ancestor.tailSegs` coming back empty) — so every entry that reaches here has
    // a real resolved identity to contribute, and the push below is unconditional.
    const resolved = missingTailSegs.length > 0 ? `${result.resolved}/${missingTailSegs.join('/')}` : result.resolved;
    resolvedEntries.push({ entry, resolved });
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

// Shared by all three identity-resolution call sites below (openCaptureRun's opening step,
// closeCaptureRun's closing step, buildProvenanceReport's current step): reuse an already-resolved
// CommandOutcome verbatim when the caller hands one back — a UI-read continuation resuming after a
// prior `needs_ui_read` — rather than invoking `d.runIdentityCommand` a second time for the same
// observation point. `undefined` (the default, and every pre-existing call site written before
// this parameter existed) means "not yet resolved, compute it now"; any other value — including a
// legitimate `null` when no command is configured at all — is used as-is, since `null` there is
// itself a resolved fact ("there is nothing to run"), not an unset sentinel.
function resolveIdentityCommandOutcome(providedOutcome, buildIdentity, d) {
  if (providedOutcome !== undefined) return providedOutcome;
  if (buildIdentity?.command) return d.runIdentityCommand(buildIdentity.command);
  return null;
}

// Shared by the same three call sites as `resolveIdentityCommandOutcome` above: resolves ONE
// observation point's build identity, converting either underlying throw into a returned halt
// rather than letting it escape past this module's declared, non-throwing contract.
//
// `d.runIdentityCommand` (reached through `resolveIdentityCommandOutcome`, which guards nothing of
// its own) is arbitrary operator shell and can throw for reasons this module has no way to
// predict. `resolveBuildIdentity` itself throws a `TypeError` on a structurally invalid
// `uiObservation.kind` (build-identity.mjs) — a shape that arrives from a UI read, which is
// untrusted input by this project's own reference doc. codex round 9 found this in
// `openCaptureRun` alone (three probes: a malformed observation, a throwing identity executor, a
// throwing `randomUUID` — the last of those is `openCaptureRun`'s own run-state construction, not
// this helper); the same exposure, unfixed, was independently measured against the real module in
// `closeCaptureRun` (reachable via a genuine open then close with a malformed closing observation,
// not merely a hypothetical) and in `buildProvenanceReport` (W6, the audit entrypoint an operator
// runs over already-merged chapters, reachable from that same untrusted UI-read source).
//
// Both throw causes land on ONE halt, `identity_resolution_threw` — matching this file's existing
// convention for "a helper this function calls threw" (see `extraction_threw` in
// `buildProvenanceReport` below) rather than the filesystem/hazard-flavored `provenance_hazard`:
// this is a resolution failure, not a disk condition, and lumping it into the hazard vocabulary
// would send an operator looking for a permissions/disk problem that is not what happened.
//
// Returns {ok: true, commandOutcome, identity} | {ok: false, halt: string, message: string}. The
// `identity` field is `resolveBuildIdentity`'s own return value UNEXAMINED — it may itself be
// `{needs_ui_read: true, ...}`; every caller below still checks that. Never throws itself.
function resolveIdentityOrHalt(identityCommandOutcome, buildIdentity, uiObservation, d) {
  let commandOutcome;
  try {
    commandOutcome = resolveIdentityCommandOutcome(identityCommandOutcome, buildIdentity, d);
  } catch (err) {
    return { ok: false, halt: 'identity_resolution_threw', message: `cannot resolve the identity command outcome: ${describeThrown(err)}` };
  }
  const uiReadEnabled = buildIdentity?.ui_read !== false;
  let identity;
  try {
    identity = resolveBuildIdentity({ commandOutcome, uiReadEnabled, uiObservation });
  } catch (err) {
    return { ok: false, halt: 'identity_resolution_threw', message: `the UI-read observation is malformed: ${describeThrown(err)}` };
  }
  return { ok: true, commandOutcome, identity };
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
// exactly the fallback for that, for every EXISTING call site (all over `run/`). Returns whether
// the unlink actually succeeded (round 6, finding 2): most callers still deliberately ignore it —
// row 6 IS the fallback for those — but `sweepChapterProvenanceTemps` has no such fallback for its
// `chapters/` temps (row 6's own `temps` observation is `run/`-only by design, see the module banner
// above that function), so a caller with nothing to fall back on needs to know a removal it is
// about to report as done did not actually happen, rather than silently believing its own report.
function unlinkBestEffort(path, deps) {
  try {
    deps.unlinkSync(path);
    return true;
  } catch {
    // best-effort only; row 6's repair states cover a temp this cleanup itself could not remove —
    // for the callers that have that fallback; see above for the one that does not.
    return false;
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
 * Open a capture run: re-assert ownership (silently), establish the provenance hierarchy, reserve
 * a one-shot pending token via an exclusive create (before this call spends the operator's identity
 * command or hashes a single asset — NOT before the three steps just named, which is what "before
 * anything else this open would do" wrongly claimed here until round 15: an invalid slug halts with
 * no token ever attempted, deliberately, so a refusal unrelated to contention leaves nothing
 * behind),
 * resolve the opening build identity, snapshot every entry's current asset-dir hashes as the
 * OPENING baseline, and finalize the reservation — writing in the run id and a digest of the
 * opening payload (never the snapshot itself — the snapshot travels in the returned `runState`,
 * which is what the cross-process serialization test protects).
 *
 * `identityCommandOutcome`, when passed, is used AS-IS instead of invoking
 * `capture.build_identity.command` again — the fix for a UI-read continuation otherwise re-running
 * the command a second time for one opening observation point (the command is arbitrary
 * operator-supplied shell: it may be slow, side-effecting, or answer DIFFERENTLY on a second run,
 * in which case the precedence chain in `resolveBuildIdentity` would resolve against a value that
 * was never the one the UI read was requested for). On a `needs_ui_read` return, this function
 * hands back the `identityCommandOutcome` it just used (computed fresh, or the one it was given) —
 * the caller performs the UI read and calls again, passing that same value straight through as
 * this parameter, so the command runs at most once per observation point regardless of how many
 * times resolution needs to be resumed. Omitting it (the pre-existing 4-argument call shape) still
 * works — the command is simply re-run on every call, exactly as before this parameter existed.
 *
 * @param {object} profileLike
 * @param {Array<{slug: string|number, group?: string}>} entries
 * @param {import('./build-identity.mjs').UiReadObservation|null} [openingObservation]
 * @param {object} [deps]
 * @param {import('./build-identity.mjs').CommandOutcome|null} [identityCommandOutcome]
 * @returns {{ok: true, runState: object}|{ok: false, halts: Array<object>}|{needs_ui_read: true, region_hint: string, identityCommandOutcome: import('./build-identity.mjs').CommandOutcome|null, warnings: string[]}}
 */
export function openCaptureRun(profileLike, entries, openingObservation, deps, identityCommandOutcome) {
  const d = mergeDeps(deps);

  const ownership = assertProvenanceOwnership(profileLike, d);
  if (ownership.skip) {
    return { ok: true, runState: { skipped: true } };
  }
  if (!ownership.ok) {
    return { ok: false, halts: ownership.halts };
  }

  const established = establishHierarchy(profileLike, d);
  // `established.hazard` is the bare `{kind, reason, path}` gate-6 shape (codex round 7, IMPORTANT
  // 1) — never pushed into `halts` raw. Wrapped with the same `halt: 'provenance_hazard'` house
  // convention every other hazard-shaped halt in this module uses (see the hierarchy-hazard sites
  // in `recordChapterProvenance`/`buildProvenanceReport`/`sweepChapterProvenanceTemps` below), so a
  // caller dispatching on the declared `Halt.halt` discriminator (capture-record.d.mts) sees
  // `'provenance_hazard'` rather than `undefined`, while `reason`/`path` survive via the spread.
  if (!established.ok) return { ok: false, halts: [{ halt: 'provenance_hazard', ...established.hazard }] };

  const validated = validateEntriesForCapture(profileLike, entries, d);
  if (!validated.ok) return validated;

  // Reserve the pending-token NAME first, via the same exclusive create the rest of this function
  // used to do LAST — a run that can never open (another token is already sitting here) now finds
  // that out via a plain EEXIST before anything else runs: neither the operator's own identity
  // command below (arbitrary, possibly side-effecting shell) nor the I/O-heavy asset-hash snapshot
  // after it executes for a run that was never going to open (codex round 8, IMPORTANT 1). Before
  // this reorder, both of those ran first and the exclusive create ran last, so a contended open
  // paid for a full identity-command invocation before ever discovering it could not open — and
  // when that command itself needed a UI read, the call returned `needs_ui_read` without having
  // attempted the token at all, sending the operator off to do UI-read legwork for a run that could
  // never open, only discovering `run_already_open` on a LATER call.
  //
  // The reservation is finalized (its real `run_id` and `opening_digest` written in) once identity
  // resolution and the snapshot have actually produced a `runState` to describe — the fd stays open
  // across both steps for exactly that reason. If either one instead needs a UI read or hits a
  // hazard, `releaseReservation` undoes the reservation before returning, so a resumed (or simply
  // retried) call gets a clean, re-triable exclusive-create check of its own rather than tripping
  // over its own predecessor's leftover reservation.
  const tokenPath = pendingTokenPath(profileLike);
  let fd;
  try {
    fd = d.openSync(tokenPath, fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_WRONLY | fs.constants.O_NOFOLLOW, 0o644);
  } catch (err) {
    if (errProp(err, 'code') === 'EEXIST') {
      return haltResult('run_already_open', 'a capture run is already open for this profile — close or abort it before opening a new one.');
    }
    return haltResult('provenance_hazard', `cannot create the pending token: ${describeThrownField(err, 'code')}`, { path: tokenPath });
  }
  // Releases the reservation this call just took, and reports whether the token is actually GONE
  // afterward — the operationally significant half of "released" (codex round 9, finding 1b): a
  // leaked `fd` costs this process one descriptor until it exits, but a token still on disk is
  // what blocks the NEXT `openCaptureRun` with `run_already_open`, so every caller below that is
  // about to leave this function on this reservation needs to know which one happened rather than
  // assuming the reservation is gone just because it tried to release it. At every call site of
  // this helper the token is still the EMPTY file just created above (nothing has been written to
  // it yet) — never valid JSON matching the schema — so row 6's classifier (`classify`,
  // `tokenState === 'invalid'`) reports it as `'partial'` regardless of whatever the run record
  // happens to hold, and `abortCaptureRun` is its repair. Returns `null` on a clean release, or a
  // warning string when the token could not be removed.
  function releaseReservation() {
    closeBestEffort(fd, d);
    if (unlinkBestEffort(tokenPath, d)) return null;
    return `the pending token '${tokenPath}' could not be removed while releasing this run's reservation — the next openCaptureRun will halt on 'run_already_open' until you run recoverProvenanceState (it will report 'partial') and abortCaptureRun to remove it.`;
  }

  // Delegates to `resolveIdentityOrHalt` (see its own comment) rather than calling
  // `resolveIdentityCommandOutcome`/`resolveBuildIdentity` directly: either can THROW, and before
  // this fix (codex round 9, finding 1a) that throw escaped this function entirely with the
  // reservation still open — the just-created token was never unlinked and the fd was never
  // closed. Nothing is committed at this point, so turning the throw into an ordinary halt loses
  // nothing a caller could have used, and keeps this function's own no-throw contract.
  const buildIdentity = profileLike.capture.build_identity ?? null;
  const resolvedOpening = resolveIdentityOrHalt(identityCommandOutcome, buildIdentity, openingObservation, d);
  if (!resolvedOpening.ok) {
    const releaseWarning = releaseReservation();
    return haltResult(resolvedOpening.halt, resolvedOpening.message, { warnings: releaseWarning ? [releaseWarning] : [] });
  }
  const { commandOutcome, identity: opening } = resolvedOpening;
  if (opening.needs_ui_read) {
    const releaseWarning = releaseReservation();
    return { ...opening, identityCommandOutcome: commandOutcome, warnings: releaseWarning ? [releaseWarning] : [] };
  }

  // Snapshotting is an I/O-heavy walk of caller-controlled directories — `snapshotAssetHashes`
  // catches ENOENT/ENOTDIR internally (an absent directory is legitimately an empty map) but
  // re-throws anything else, and an earlier version of this function had NOTHING catching that,
  // so an unexpected errno (EACCES, EIO, ...) crashed the whole call with an uncaught exception
  // instead of returning a halt (codex, important #6).
  const openingAssets = {};
  const openingHazards = {};
  try {
    for (const entry of entries) {
      const assetDir = chapterAssetDir(profileLike, entry);
      const snapshot = snapshotAssetHashes(assetDir, d);
      openingAssets[chapterKeyFor(entry)] = snapshot.hashes;
      openingHazards[chapterKeyFor(entry)] = snapshot.hazards;
    }
  } catch (err) {
    const releaseWarning = releaseReservation();
    return haltResult('provenance_hazard', `cannot snapshot the opening asset hashes: ${describeThrownField(err, 'code')}`, { warnings: releaseWarning ? [releaseWarning] : [] });
  }

  let runState;
  try {
    runState = {
      skipped: false,
      run_id: d.randomUUID(),
      opening,
      opening_assets: openingAssets,
      opening_asset_hazards: openingHazards,
      entries: entries.map(entryKeyShape),
    };
    runState.opening_digest = digestOpeningPayload(openingPayloadFromRunState(runState));
  } catch (err) {
    // `randomUUID()` and `digestOpeningPayload` both throw rather than return a result (the
    // latter documented on its own declaration) — this construction sits after the same
    // reservation as identity resolution above, so an unhandled throw here leaked the fd and the
    // token exactly the same way (codex round 9, finding 1a, the "throwing randomUUID" probe).
    const releaseWarning = releaseReservation();
    return haltResult('provenance_hazard', `cannot construct the run state: ${describeThrown(err)}`, { warnings: releaseWarning ? [releaseWarning] : [] });
  }
  const digest = runState.opening_digest;

  const tokenText = JSON.stringify({ run_id: runState.run_id, opening_digest: digest });
  try {
    writeFull(fd, Buffer.from(tokenText, 'utf8'), d);
  } catch (err) {
    // A throwing (or short) write here previously escaped this function entirely — the surrounding
    // `try` had only a `finally` closing the fd, never a `catch`, so an ordinary write failure
    // became an UNCAUGHT exception instead of the returned `{ok:false, halts}` this module's
    // contract promises everywhere else, and the just-created (O_CREAT|O_EXCL) token was left
    // behind with no caller ever having a chance to clean it up. Best-effort: this closeSync
    // failing must never MASK the write failure we are about to report (codex round 3).
    const releaseWarning = releaseReservation();
    return haltResult('provenance_hazard', `cannot write the pending token: ${describeThrownField(err, 'reason', 'code')}`, { path: tokenPath, warnings: releaseWarning ? [releaseWarning] : [] });
  }
  try {
    d.closeSync(fd);
  } catch (err) {
    // The token was fully written (writeFull succeeded) but a failing close means it cannot be
    // trusted as durably flushed — untrust it outright rather than returning ok:true over an
    // uncertain token (some filesystems can fail a close after acknowledging the write). The token
    // now holds this run's real (valid) run_id/opening_digest rather than the empty file
    // `releaseReservation` above assumes, so a failed unlink here leaves row 6's classifier
    // reporting `'open'` (or `'prepared'` if a leftover temp also happens to survive), never
    // `'partial'` — named explicitly here rather than reusing `releaseReservation`'s wording, which
    // would be the wrong state at this specific site.
    const removed = unlinkBestEffort(tokenPath, d);
    const warnings = removed
      ? []
      : [
          `the pending token '${tokenPath}' could not be removed after being written (the close that failed came after a successful write) — the next openCaptureRun will halt on 'run_already_open' until you run recoverProvenanceState (it will report 'open') and abortCaptureRun to remove it.`,
        ];
    return haltResult('provenance_hazard', `cannot close the pending token after writing it: ${describeThrownField(err, 'code')}`, { path: tokenPath, warnings });
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
  // [round 15] `asset_hazards` is authenticated too. It is the record of what could NOT be
  // established at open, and W5 refuses on it — so leaving it outside the digest would let a caller
  // clear the one field that blocks a confident record, which is precisely the forgery this digest
  // exists to stop.
  return {
    entries: runState.entries,
    assets: runState.opening_assets,
    asset_hazards: runState.opening_asset_hazards,
    identity: runState.opening,
  };
}

// The one recursive asset-tree walk both sweeps below share (the hash snapshot and the filename
// listing) — a single definition of "which files under an asset directory this feature can see",
// rather than two copies free to drift apart on the symlink or the errno rule. A symlink is never
// followed, as a directory to descend or as a file to visit. ENOENT/ENOTDIR ends that branch
// quietly (an asset directory that does not exist yet is legitimately empty — W2 snapshots the
// opening baseline before the capture command has written anything); every OTHER errno propagates
// to the caller, which turns it into a halt rather than a silently short list.
// [round 16] `onSkipped` reports an entry the walk REFUSES to visit but that is nevertheless THERE.
// Round 15 split hazard from absence inside `snapshotAssetHashes`, but only files the walk actually
// visited could reach that classification — a symlink was dropped one level higher, here, and so
// came out the far end as an absence again. W5 reads an absent OPENING key as "brand-new file this
// run" and skips the did-it-change check, so an asset that was a symlink to stale bytes at open and
// a plain file with those same stale bytes at close was recorded as this build's. The distinction
// the previous round drew was right; it was drawn one layer too low. A caller that does not care
// (the filename listing, which is asking which assets exist, not which could be hashed) omits it.
/**
 * What a directory entry actually is: `symlink`, `directory`, `file`, `absent`, `non_regular` or
 * `inspection_failure`.
 *
 * [round 18] A dirent's type can be UNKNOWN. libuv reports `UV_DIRENT_UNKNOWN` on filesystems that
 * do not fill in `d_type` — several network and FUSE mounts, and XFS in some configurations — and
 * then EVERY predicate on the dirent is false, `isFile()` included. Treating "not a symlink, not a
 * directory, not a file" as *therefore* a device node was a confident label for something never
 * established, and it dropped the entry: on such a filesystem every plain `a.png` disappears from
 * both the hash snapshot and the filename listing, so extraction halts on a destination it cannot
 * match and no chapter can be recorded at all. Unknown means the kernel declined to answer from the
 * directory block, and the answer is to ask — one `lstat`, only on the entries that need it, so a
 * filesystem that does report types pays nothing. The concrete non-regular types are checked first
 * for exactly that reason. Every predicate is called optionally: a caller may inject a dirent that
 * implements only the three this module used to consult, and the fail-safe direction for a missing
 * predicate is to go and find out rather than to throw.
 */
function direntType(dirent, absPath, deps) {
  if (dirent.isSymbolicLink?.()) return 'symlink';
  if (dirent.isDirectory?.()) return 'directory';
  if (dirent.isFile?.()) return 'file';
  if (dirent.isSocket?.() || dirent.isFIFO?.() || dirent.isCharacterDevice?.() || dirent.isBlockDevice?.()) {
    return 'non_regular';
  }
  let st;
  try {
    st = deps.lstatSync(absPath);
  } catch (err) {
    return errProp(err, 'code') === 'ENOENT' ? 'absent' : 'inspection_failure';
  }
  if (st.isSymbolicLink()) return 'symlink';
  if (st.isDirectory()) return 'directory';
  if (st.isFile()) return 'file';
  return 'non_regular';
}

function walkRegularFiles(rootDir, deps, visit, onSkipped) {
  walk(rootDir, '');

  function walk(absDir, relPrefix) {
    let entries;
    try {
      entries = deps.readdirSync(absDir, { withFileTypes: true });
    } catch (err) {
      const code = errProp(err, 'code');
      // ENOENT: nothing is there. The one condition this module has always called an absence.
      if (code === 'ENOENT') return;
      // [round 18] ENOTDIR used to return here too, silently, and that was the round-17 defect
      // through a different door: a subdirectory that was a directory when its type was decided and
      // a regular file a moment later took everything beneath it out of the snapshot with no
      // hazard recorded. At the OPENING observation point those absent keys read as "brand-new this
      // run", rule 4 is skipped, and old bytes are recorded as the captured build's. The fail-closed
      // argument that covers the filename listing does not cover this — the listing halts on a
      // destination it cannot match, while the opening snapshot reads a missing key as good news.
      // A hazard on the directory itself now covers every asset under it, which is only expressible
      // because the W5 match became containment in the same round.
      //
      // The ROOT call is a separate case and is NOT nameable as a relative path: it is gated at the
      // call site, which lstats the asset directory and halts on anything but ENOENT/ENOTDIR.
      // Any other code still throws, which halts the whole run — strictly more conservative than a
      // per-directory hazard, so it is left as it was.
      if (code === 'ENOTDIR') {
        if (relPrefix !== '') onSkipped?.(relPrefix, 'inspection_failure');
        return;
      }
      throw err;
    }
    for (const dirent of entries) {
      const childAbs = posixJoin(absDir, dirent.name);
      const childRel = relPrefix ? `${relPrefix}/${dirent.name}` : dirent.name;
      const type = direntType(dirent, childAbs, deps);
      if (type === 'symlink') { onSkipped?.(childRel, 'symlink'); continue; }
      if (type === 'directory') { walk(childAbs, childRel); continue; }
      if (type === 'file') { visit(childAbs, childRel); continue; }
      // The entry vanished between the listing and the check. That is the one condition this
      // module has always classified as an ABSENCE rather than a hazard — nothing was there to
      // establish — and the leaf layer would answer the same way if it were visited.
      if (type === 'absent') continue;
      // Present, unreadable as an asset, and silently dropped until this branch existed — the same
      // shape as the symlink case and reachable the same way. `non_regular` and
      // `inspection_failure` are spelled exactly as the leaf inspection spells them: the two layers
      // observe the same facts at different moments, and until the reason words reached operators
      // (round 17) the difference was invisible, because the leaf's word was collapsed to `hazard`
      // before anyone could read it. Two spellings of one condition is a distinction an operator
      // would have to look up to learn is not a distinction.
      onSkipped?.(childRel, type);
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
// a symlink.
//
// [round 15] The paragraph above used to end by calling the exclusion harmless: "a missing expected
// image fails completeness (rule 3) and the chapter is reported ineligible, never silently
// trusted." That is true of the CLOSING snapshot, which is what rule 3 reads. It is the exact
// opposite for the OPENING one, where a missing key means "brand-new file this run" and so SKIPS
// rule 4 — the check that the bytes changed during capture. One function serves both observation
// points, and the justification was written for one of them. The consequence was a confident record
// over stale bytes: an asset carrying old-build bytes and an extra hard link at open is dropped from
// the opening snapshot; the capture removes only the alias, never the bytes; closing hashes it
// fine; W5 reads the absent opening key as brand-new, skips rule 4, finds the re-hash equal to
// closing, and attributes the old bytes to the current build. So a hazard is no longer encoded as
// an absence: it is returned separately, and W5 refuses any asset that was hazardous at either
// point, because "we could not read this file then" is not evidence that it changed.
function snapshotAssetHashes(assetDir, deps) {
  const hashes = Object.create(null);
  const hazards = [];
  walkRegularFiles(
    assetDir,
    deps,
    (absPath, relPath) => {
      const hashed = hashFileNoFollow(absPath, deps);
      if (hashed.kind === 'present') hashes[relPath] = hashed.digest;
      // 'absent' (the file vanished between listing and open) stays an absence: nothing was there
      // to establish, and a file the capture then creates is legitimately brand new. 'hazard' does
      // not — the file WAS there and we were refused, which is a different fact and must survive
      // as one.
      else if (hashed.kind !== 'absent') hazards.push(`${relPath}:${unreadableWord(hashed)}`);
    },
    // A listed entry the walk would not even open is a hazard for the same reason: something is at
    // that path whose bytes this run could not establish.
    (relPath, kind) => hazards.push(`${relPath}:${kind}`),
  );
  hazards.sort();
  return { hashes, hazards };
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
// than crash either way. Generalized (codex round 5, finding 3) so `sweepChapterProvenanceTemps`'s
// chapter-namespace listing shares this exact readdir/ENOENT/errno handling rather than a second,
// possibly-drifting copy — the two differ only in which directory and which filename pattern they
// list, never in how a listing failure is reported.
function listMatchingTempsIn(dir, prefix, suffix, deps) {
  let entries;
  try {
    entries = deps.readdirSync(dir);
  } catch (err) {
    if (errProp(err, 'code') === 'ENOENT') return { ok: true, temps: [] };
    return { ok: false, hazard: { kind: 'hazard', reason: 'inspection_failure', path: dir } };
  }
  const temps = entries.filter((name) => name.startsWith(prefix) && name.endsWith(suffix)).map((name) => posixJoin(dir, name));
  return { ok: true, temps };
}

function listMatchingTemps(profileLike, deps) {
  return listMatchingTempsIn(runNamespaceDir(profileLike), `${RUN_RECORD_NAME}.`, '.tmp', deps);
}

// A chapter's own `<slug>.json.<uuid>.tmp` temps — the same filename shape `recordChapterProvenance`
// builds at its own write site (`tempPath = \`${finalPath}.${d.randomUUID()}.tmp\``), listed in that
// chapter's own directory (`chapterRecordDir`, shared with `chapterRecordPath`) rather than
// `run/current.json`'s namespace — chapter temps and run temps are never the same list (finding 3).
function listMatchingChapterTemps(profileLike, entry, deps) {
  const dir = chapterRecordDir(profileLike, entry);
  const fileName = `${String(entry.slug)}.json`;
  return listMatchingTempsIn(dir, `${fileName}.`, '.tmp', deps);
}

/**
 * Close a capture run: re-verify the token matches this `runState`, resolve the closing build
 * identity, snapshot the CLOSING asset hashes, combine the two halves into the run's final recorded
 * identity, write the run
 * record to a process-unique temp under `run/`, commit by rename, then remove every leftover
 * matching temp and, ONLY once every one of them is confirmed gone, the token (codex round 7,
 * IMPORTANT 2 — a temp whose removal could not be confirmed leaves the token in place on purpose,
 * so the next `openCaptureRun` halts on it and forces `recoverProvenanceState`/
 * `cleanupCommittedRun` rather than silently reporting a clean close over a stuck temp). Never
 * throws on an ordinary failure — every exit is a returned `{ok:false, halts}`, so a caller
 * branches on `halts` rather than relying on an exception.
 *
 * `identityCommandOutcome` is the closing counterpart of `openCaptureRun`'s parameter of the same
 * name — see that function's doc comment for why a UI-read continuation must reuse the CLOSING
 * step's already-resolved command outcome rather than re-invoking `capture.build_identity.command`
 * a second time, and how the value returned alongside a `needs_ui_read` result is meant to be
 * threaded straight back in on the retry call.
 *
 * The returned `warnings` array also carries an operator-facing line whenever the run's FINAL
 * recorded identity did not cleanly resolve to a value — a missing, failing, unconfirmed or
 * changed identity (SKILL.md's own "W2 warns... on any of these outcomes"), via
 * `describeBuildIdentityWarning` (build-identity.mjs). Emitted here, once, on the committed
 * result — never from `openCaptureRun`, since the run's FINAL identity (what a `build_unconfirmed`
 * or `build_changed_during_capture` verdict actually is) is only known once both the opening and
 * the closing resolutions and `captureOutcome` have all been combined, which happens in THIS
 * function; an opening-side warning would either have to guess at the final verdict or duplicate
 * this same check before it can be answered.
 *
 * @param {object} profileLike
 * @param {object} runState
 * @param {{ok: boolean, detail?: string}} captureOutcome
 * @param {import('./build-identity.mjs').UiReadObservation|null} [closingObservation]
 * @param {object} [deps]
 * @param {import('./build-identity.mjs').CommandOutcome|null} [identityCommandOutcome]
 * @returns {{ok: true, runState: object, warnings: string[]}|{ok: false, halts: Array<object>}|{needs_ui_read: true, region_hint: string, identityCommandOutcome: import('./build-identity.mjs').CommandOutcome|null}}
 */
export function closeCaptureRun(profileLike, runState, captureOutcome, closingObservation, deps, identityCommandOutcome) {
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
  // [round 14, ped-ant] Guarded, and the reason is the paragraph above: this treats `runState` as
  // tamperable serialized input, and `digestOpeningPayload` THROWS on a member it cannot
  // canonicalize rather than returning a result. A payload whose `entries`/`opening`/
  // `opening_assets` was DELETED (as opposed to the mutation the tampering test covers) therefore
  // escaped this function as an exception, out of a contract that says every ordinary failure comes
  // back as `{ok: false, halts}` — and it escaped before anything durable was written, leaving the
  // pending run to be recovered by hand. Round 9 deliberately left this call site unguarded on the
  // reasoning that `runState` is internal state that provably cannot throw on the legitimate path;
  // the legitimate path was never the question here, since the whole point of the digest check is
  // the ILLEGITIMATE one. A payload that cannot be canonicalized cannot match the token's digest
  // either, so it takes the same `stale_replay` exit as any other non-matching payload.
  let recomputedDigest;
  try {
    recomputedDigest = digestOpeningPayload(openingPayloadFromRunState(runState));
  } catch (err) {
    return haltResult(
      'stale_replay',
      `this runState's opening payload cannot be canonicalized, so it cannot match the token's stored digest (${describeThrown(err)}) — re-derive with recoverProvenanceState.`,
    );
  }
  if (recomputedDigest !== parsedToken.value.opening_digest) {
    return haltResult(
      'stale_replay',
      'this runState\'s opening payload does not match the token\'s stored digest — the payload was altered after opening, or this runState belongs to a different (possibly no-longer-open) run.',
    );
  }

  const buildIdentity = profileLike.capture.build_identity ?? null;
  // The closing observation runs the SAME three-step resolution order as the opening one — the
  // identity command is re-invoked, not skipped, since a command-configured profile must resolve
  // its closing identity from the command too, not fall straight to the UI-read fallback. "Re-
  // invoked" means at most once per closing observation point, though: `identityCommandOutcome`
  // (a UI-read continuation resuming this same close) is reused verbatim instead, exactly as
  // `openCaptureRun` reuses its own opening-side parameter.
  //
  // Delegates to `resolveIdentityOrHalt` rather than calling `resolveIdentityCommandOutcome`/
  // `resolveBuildIdentity` directly: either can THROW (see that helper's own comment), and a
  // malformed closing observation or a throwing identity command previously escaped this function
  // uncaught — measured against the real module (codex round 9 follow-up), reachable via a genuine
  // open then close with a malformed closing observation, not merely a hypothetical. Nothing
  // durable has been written at this point (the temp/rename sequence is further down), so a halt
  // here loses nothing a caller could have used.
  const resolvedClosing = resolveIdentityOrHalt(identityCommandOutcome, buildIdentity, closingObservation, d);
  if (!resolvedClosing.ok) return haltResult(resolvedClosing.halt, resolvedClosing.message, {});
  const { commandOutcome: closingCommandOutcome, identity: closing } = resolvedClosing;
  if (closing.needs_ui_read) return { ...closing, identityCommandOutcome: closingCommandOutcome };

  // Same reasoning as the opening sweep: an unexpected errno during the closing sweep must return
  // a halt, not crash — and doing so HERE (before any temp is written) is what keeps the existing
  // "no record written before the closing resolution" guarantee true on this exit too.
  const closingAssets = {};
  const closingHazards = {};
  try {
    for (const entry of runState.entries) {
      const assetDir = chapterAssetDir(profileLike, entry);
      const snapshot = snapshotAssetHashes(assetDir, d);
      closingAssets[chapterKeyFor(entry)] = snapshot.hashes;
      closingHazards[chapterKeyFor(entry)] = snapshot.hazards;
    }
  } catch (err) {
    return haltResult('provenance_hazard', `cannot snapshot the closing asset hashes: ${describeThrownField(err, 'code')}`, {});
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
      opening_hazards: runState.opening_asset_hazards?.[key] ?? [],
      closing_hazards: closingHazards[key] ?? [],
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

  const finalPath = runRecordPath(profileLike);

  // `tempPath` is now computed INSIDE the try, not before it: `tempRunRecordPath` calls
  // `deps.randomUUID()` with no guard of its own, and a throw there previously escaped this
  // function entirely — before a temp name even existed, let alone anything written (codex round 9
  // follow-up; the same class of gap the round fixed in openCaptureRun, measured here too, not
  // merely reasoned from the code's shape). `tempPath` staying `undefined` is how the catch below
  // tells "never named" apart from "named but the write/open failed".
  let tempPath;
  let fd;
  try {
    tempPath = tempRunRecordPath(profileLike, d);
    fd = d.openSync(tempPath, fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_WRONLY | fs.constants.O_NOFOLLOW, 0o644);
    writeFull(fd, Buffer.from(recordText, 'utf8'), d);
  } catch (err) {
    if (fd !== undefined) {
      // Best-effort: this closeSync failing must never MASK the write failure we are about to
      // report (a throwing catch body would silently replace it — codex round 3).
      closeBestEffort(fd, d);
    }
    if (tempPath !== undefined) {
      unlinkBestEffort(tempPath, d); // a create that succeeded but a write that failed leaves a
      // partial temp on disk — remove it rather than leaving litter for the failure path to answer for.
    }
    const detail = tempPath === undefined ? `cannot generate the closing temp name: ${describeThrown(err)}` : `cannot write the closing temp: ${describeThrownField(err, 'code')}`;
    return haltResult('provenance_hazard', detail, { path: tempPath });
  }
  try {
    d.closeSync(fd);
  } catch (err) {
    // A close failure right here means the fully-written temp is not yet renamed to its final
    // name — nothing durable has been committed at this point, so this is an ordinary halt (with
    // best-effort cleanup of the temp), not the post-commit case the cleanup loop below answers to.
    unlinkBestEffort(tempPath, d);
    return haltResult('provenance_hazard', `cannot close the closing temp after writing it: ${describeThrownField(err, 'code')}`, { path: tempPath });
  }

  try {
    d.renameSync(tempPath, finalPath);
  } catch (err) {
    unlinkBestEffort(tempPath, d); // the rename itself failed — the fully-written temp is still at
    // its OWN name, never at finalPath, so removing it leaves zero surviving temps on this exit.
    return haltResult('provenance_hazard', `cannot commit the run record: ${describeThrownField(err, 'code')}`, { path: finalPath });
  }

  // A missing, failing, unconfirmed or changed FINAL identity is a warning here, on the
  // already-committed result — SKILL.md's own "W2 warns... on any of these outcomes" promise, kept
  // for real this time: before this, the only place any of that ever landed was the committed
  // record's own `build_identity.detail` field, which nothing production-side reads unprompted.
  // `describeBuildIdentityWarning` returns `null` on a clean resolution (`resolution_reason: null`)
  // — nothing pushed in that case.
  const warnings = [];
  const identityWarning = describeBuildIdentityWarning({ opening: runState.opening, closing, final: finalIdentity, captureOutcome });
  if (identityWarning) warnings.push(identityWarning);

  // Cleanup: every leftover matching temp first, the token last — the same order as the row-6
  // repairs, but NOT the same unconditional-token-removal contract those two make (SKILL.md's
  // "both repairs remove every leftover temp first and the token last" is a promise about
  // `abortCaptureRun`/`cleanupCommittedRun` specifically, not about this cleanup). The rename above
  // has ALREADY committed the run record durably — a hazard here, of either kind below, must never
  // be reported as if nothing was written (codex round 3): it is a WARNING on this still-`ok:true`
  // result, never a halt.
  //
  // The token, though, is removed ONLY once every matching temp is CONFIRMED gone (codex round 7,
  // IMPORTANT 2) — not unconditionally best-effort as `unlinkBestEffort`'s own name would suggest.
  // Before this, a temp whose `unlinkSync` genuinely threw (or a listing hazard that left every
  // temp's fate unknown) still let the token get removed right after: with the token gone and a
  // temp still on disk, row 6 classifies the tree `orphan_temp` — a state nothing but an operator
  // manually running `recoverProvenanceState` will ever discover, since the very success this call
  // reports removed the one signal (a pre-existing token) that would make a FUTURE `openCaptureRun`
  // stop and ask. Retaining the token instead keeps the token/record pair matching (same run_id,
  // same opening_digest), which classifies as `committed` — a state whose own repair
  // (`cleanupCommittedRun`) re-verifies that fingerprint before touching anything, unlike
  // `orphan_temp`'s `abortCaptureRun`, which is a blind sweep by design (no token left to check one
  // against). And critically, a token left in place makes the NEXT `openCaptureRun` call halt on
  // `run_already_open` (its pending-token create is an exclusive O_CREAT|O_EXCL, so ANY token on
  // disk blocks it) — the forcing function that gets the operator to `recoverProvenanceState` at
  // all, rather than silently opening a new run over an unresolved stuck temp. `cleanupIncomplete`
  // covers both hazard kinds uniformly: a listing failure is exactly as uncertain about "is
  // everything really gone" as a confirmed-failed unlink, so both withhold the token the same way.
  const tempsListed = listMatchingTemps(profileLike, d);
  let cleanupIncomplete = false;
  if (tempsListed.ok) {
    for (const temp of tempsListed.temps) {
      if (unlinkBestEffort(temp, d)) continue;
      cleanupIncomplete = true;
      warnings.push(
        `leftover run-record temp '${temp}' could not be removed and remains on disk — the pending token has been left in place so the next openCaptureRun halts on it; run recoverProvenanceState (it will report 'committed') and cleanupCommittedRun to resolve it.`,
      );
    }
  } else {
    cleanupIncomplete = true;
    warnings.push(
      `the run record committed successfully, but leftover temps under 'run/' could not be listed for cleanup (${tempsListed.hazard.reason} at '${tempsListed.hazard.path}') — the pending token has been left in place so the next openCaptureRun halts on it; run recoverProvenanceState to check for and remove any leftover temp.`,
    );
  }
  if (!cleanupIncomplete && !unlinkBestEffort(tokenPath, d)) {
    // Every temp WAS confirmed gone, so `cleanupIncomplete` alone can no longer explain a token
    // still on disk — this is the token's OWN removal failing (EACCES, EROFS, ...), the one
    // asymmetry left after the loop above already started checking `unlinkBestEffort`'s return: the
    // temps were checked, the token itself was not (codex round 8, IMPORTANT 2). Left silent, the
    // token stays exactly as long as it would after any other cleanup hazard, but with nothing in
    // `warnings` telling the operator why the next `openCaptureRun` halts on `run_already_open`.
    warnings.push(
      `the pending token '${tokenPath}' could not be removed after every temp was confirmed gone — it will make the next openCaptureRun halt on 'run_already_open' even though this run committed cleanly; run recoverProvenanceState (it will report 'committed') and cleanupCommittedRun to remove it.`,
    );
  }

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
    // [round 16] REQUIRED, and validated, both of them. The hazard lists arrived with the
    // hazard/absence split one round earlier, and the reader kept accepting records without them
    // under the same `record_version: 1` — so a record written before the split reads back as
    // "no hazards", which is exactly the false statement the split exists to prevent: W5 then sees
    // no hazard and no opening hash, calls the asset brand-new, and writes the confident record.
    // Absence is not a safe default for a field whose whole content is "here is what we could not
    // establish". Two version-1 shapes cannot both be valid; the older one is now malformed, which
    // W6 reports as `record_malformed` and W5 refuses on, rather than silently trusting.
    for (const field of ['opening_hazards', 'closing_hazards']) {
      const list = entry[field];
      if (!Array.isArray(list)) return { ok: false, reason: `bad_chapter_hazards:${field}` };
      // Validated element-wise too: a non-string member would reach `.find()`/`.slice()` in W5 and
      // throw out of a function whose contract is a returned reason.
      // [round 17, found by the repository's cross-file review bot] Checking only the JavaScript
      // TYPE re-opened, through a malformed member, exactly the false-provenance path the hazard
      // lists exist to close. `"a.png"` with no colon is a string, so it validated; `hazardFor`
      // splits at the LAST colon, `lastIndexOf` returns -1, and `slice(0, -1)` yields `a.pn` —
      // which matches no asset key, so the hazard is silently ignored, the missing opening hash
      // reads as "brand-new this run", and rule 4 is skipped over old bytes. A member that cannot
      // be interpreted must invalidate the record rather than be dropped: an unverifiable run
      // record refuses every chapter, which is the fail-closed direction.
      if (list.some((h) => !isWellFormedHazard(h))) return { ok: false, reason: `bad_chapter_hazards:${field}` };
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
 * expected image's `closing` hash differs from `opening`; and a fresh re-hash right now EQUALS
 * `closing`. Any failure ⇒ no record written, the chapter's existing record (if any) is left
 * byte-identical, and the failing rule is returned as a warning.
 *
 * [round 14] That last rule used to read "still differs from `opening`", here and in SKILL.md and
 * in the code. Differing from the opening admits bytes the captured build never produced, which the
 * record would then attribute to it; equality with `closing` is the property that makes the record
 * mean anything, and it subsumes the old check because rule 4 has already established that closing
 * differs from opening. This block is the EXPORTED contract, so a caller reading only the API saw
 * the obsolete rule after the other three sites were corrected.
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
  // Same defect as `openCaptureRun`'s hierarchy-establishment site above (codex round 7, IMPORTANT
  // 1): `groupDirEstablished.hazard` is the bare gate-6 shape, wrapped with the same
  // `halt: 'provenance_hazard'` convention rather than pushed raw.
  if (!groupDirEstablished.ok) return { ok: false, halts: [{ halt: 'provenance_hazard', ...groupDirEstablished.hazard }] };

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
    return { recorded: false, reason: `chapter_read_failed:${describeThrownField(err, 'code')}` };
  }

  const assetDir = chapterAssetDir(profileLike, entry);
  let filenames;
  try {
    filenames = listRegularFilesRecursive(assetDir, d);
  } catch (err) {
    return { recorded: false, reason: `asset_listing_failed:${describeThrownField(err, 'code')}` };
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
    return { recorded: false, reason: `extraction_threw:${describeThrown(err)}` };
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

  // [round 15] Rule 3.5, checked FIRST because it decides whether the other rules mean anything: an
  // asset that could not be hashed at open or at close has no established bytes at that point, so
  // neither "it changed during capture" (rule 4) nor "it is brand new" can be concluded about it.
  // The reason names the asset and HOW it was unreadable rather than collapsing to one word — an
  // operator reading `hard_link` acts differently than one reading `inspection_failure`. That
  // sentence was false for two rounds: the persisted word was the leaf inspection's `kind`, which
  // is `hazard` for all four of them. See `unreadableWord`.
  // [round 17] The lookup is CONTAINMENT, not equality, and the difference is a shipped defect: the
  // walk refuses a symlinked DIRECTORY under that directory's own path (`screens:symlink`), while
  // the asset it hides is keyed `screens/a.png`. An equality match filed the hazard under a name it
  // was never looked up by — so the refusal never fired, the opening map had no entry either (the
  // walk never reached the file), and rule 4 read that absence as "brand-new this run". A hazard is
  // a statement about a PATH: withholding a directory withholds the bytes of everything beneath it.
  // The trailing separator is load-bearing — a bare `startsWith` would also swallow `screensaver/`,
  // whose bytes this run established perfectly well.
  const hazardFor = (list, key) => (list ?? []).find((h) => {
    const path = h.slice(0, h.lastIndexOf(':'));
    return key === path || key.startsWith(`${path}/`);
  });
  for (const asset of extraction.assets) {
    const openingHazard = hazardFor(chapterRunData.opening_hazards, asset.key);
    if (openingHazard !== undefined) {
      return { recorded: false, reason: `rule5_opening_unhashable:${openingHazard}` };
    }
    const closingHazard = hazardFor(chapterRunData.closing_hazards, asset.key);
    if (closingHazard !== undefined) {
      return { recorded: false, reason: `rule5_closing_unhashable:${closingHazard}` };
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

  // Rule 5: re-hash every expected image now and require it to still BE the bytes this run closed
  // over. [round 13] The rule used to require only that the re-hash differ from `opening`, which
  // rejects a revert to the pre-capture bytes and nothing else: a replacement with any third value
  // differs from the opening too, so it passed, and its hash was then persisted under this run's
  // `build_identity` — W6 afterwards reported it verified, because the bytes on disk matched the
  // hash that had just been recorded from them. What the record claims is that these bytes came
  // from the captured build, and only `closing` carries that. Comparing against `closing` subsumes
  // the old check: rule 4 above has already established closing !== opening, so bytes equal to
  // closing cannot equal opening. The revert stays a distinct reason because it is the more
  // specific diagnosis of the same failure and names what the operator most likely did.
  const assetHashes = Object.create(null);
  for (const asset of extraction.assets) {
    const rehash = hashFileNoFollow(asset.absPath, d);
    if (rehash.kind !== 'present') {
      // [round 15] Names the asset, not just the kind. Every hazard used to reduce to
      // `rehash_failed:hazard`, so an operator holding an ineligible chapter with several embeds
      // learned neither which file nor why.
      return { recorded: false, reason: `rehash_failed:${asset.key}:${unreadableWord(rehash)}` };
    }
    assetHashes[asset.key] = rehash.digest;
    if (rehash.digest === chapterRunData.closing[asset.key]) continue;
    const openingHash = chapterRunData.opening[asset.key];
    if (openingHash !== undefined && rehash.digest === openingHash) {
      return { recorded: false, reason: 'rule5_reverted_to_opening' };
    }
    return { recorded: false, reason: 'rule5_replaced_since_closing' };
  }

  const chapterRecord = {
    record_version: 1,
    run_id: expectedRunId,
    build_identity: runRecord.record.build_identity,
    asset_hashes: assetHashes,
  };
  const recordText = JSON.stringify(chapterRecord, null, 2);

  const finalPath = chapterRecordPath(profileLike, entry);
  // `tempPath` is computed INSIDE the try, not before it: `d.randomUUID()` is called with no guard
  // of its own, and a throw there previously escaped this function entirely — before a temp name
  // even existed, let alone anything written (codex round 9 follow-up, measured against the real
  // module, not merely reasoned from the code's shape). `tempPath` staying `undefined` is how the
  // catch below tells "never named" apart from "named but the write/open failed".
  let tempPath;
  let fd;
  try {
    tempPath = `${finalPath}.${d.randomUUID()}.tmp`;
    fd = d.openSync(tempPath, fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_WRONLY | fs.constants.O_NOFOLLOW, 0o644);
    writeFull(fd, Buffer.from(recordText, 'utf8'), d);
  } catch (err) {
    if (fd !== undefined) {
      // Best-effort: this closeSync failing must never MASK the write failure we are about to
      // report (codex round 3).
      closeBestEffort(fd, d);
    }
    if (tempPath !== undefined) unlinkBestEffort(tempPath, d);
    // Assigned into a returned FIELD rather than interpolated, so a non-string `.reason` (a thrown
    // `{reason: Symbol(...)}`) would not throw here — but it would still put a Symbol into a field
    // this module's own convention treats as a plain string, silently dropped by a caller's
    // `JSON.stringify`. Typed-checked the same way as every message-building site now, not just the
    // ones that would crash outright.
    const errReason = errProp(err, 'reason');
    const reason = tempPath === undefined ? 'temp_name_generation_failed' : typeof errReason === 'string' ? errReason : 'write_failed';
    return { ok: false, halts: [{ halt: 'provenance_hazard', reason, path: tempPath, detail: describeThrown(err) }] };
  }
  try {
    d.closeSync(fd);
  } catch (err) {
    // Nothing durable has been committed yet at this point (the rename below hasn't run) — an
    // ordinary halt, with best-effort cleanup of the still-fully-written-but-unclosed temp.
    unlinkBestEffort(tempPath, d);
    return { ok: false, halts: [{ halt: 'provenance_hazard', reason: 'close_failed', path: tempPath, detail: describeThrown(err) }] };
  }
  try {
    d.renameSync(tempPath, finalPath);
  } catch (err) {
    unlinkBestEffort(tempPath, d);
    return { ok: false, halts: [{ halt: 'provenance_hazard', reason: 'rename_failed', path: finalPath, detail: describeThrown(err) }] };
  }

  return { recorded: true, reason: null };
}

function readFileText(path, deps) {
  const read = readLeafText(path, deps);
  if (read.kind === 'present') return read.text;
  throw new Error(`cannot read ${path}: ${unreadableWord(read)}`);
}

// Deliberately passes NO `onSkipped`, and that is not an oversight of the same class the snapshot
// just fixed. This list answers "which files can serve as an asset", and a symlink or a device node
// cannot; the snapshot answers "what were this asset's bytes at this moment", where being unable to
// say is itself the fact that matters. The consequence of the omission here is already fail-closed:
// a chapter embedding a symlinked image finds no candidate to match, so extraction halts with an
// unmatched destination and the chapter is reported ineligible — it is never recorded on the
// strength of a file this feature declined to look at. Written down because the walk's two callers
// have now been the subject of three consecutive review rounds.
function listRegularFilesRecursive(assetDir, deps) {
  const out = [];
  walkRegularFiles(assetDir, deps, (_absPath, relPath) => out.push(relPath));
  return out;
}

// ---------------------------------------------------------------------------------------------
// sweepChapterProvenanceTemps — codex round 5, finding 3. A process that dies between
// `recordChapterProvenance`'s temp write (above) and its rename leaves `<slug>.json.<uuid>.tmp`
// behind under `chapters/`. Row 6's classifier could not see this: its `temps` observation
// (`inspectTokenAndRecordAndTemps`) lists ONLY `run/current.json.*.tmp`, and that is not an
// oversight to widen but a boundary to keep. A chapter temp is not correlated with the run's own
// token/record the way a `run/` temp is — `recordChapterProvenance` may run, and crash, many times
// per run (once per chapter) or even after the run has already cleanly committed, since its only
// precondition is that the run record's `run_id` still matches; a chapter temp can therefore be
// found, or be absent, under ANY of the nine row-6 states with no correlation to which one. Folding
// it into the SAME `temps` tuple `classify` switches on would make `hasTemps` conflate two
// unrelated leftovers — an `absent` run with one unrelated stray chapter temp would misclassify as
// `orphan_temp` FOREVER, since nothing about closing that chapter temp ever changes the run's own
// token/record — and `abortCaptureRun`/`cleanupCommittedRun` are documented (SKILL.md's recovery
// table) as NEVER touching `chapters/`, which widening the tuple would either silently break or
// require quietly contradicting. So row 6's domain statement — a TOTAL function of
// `(token, record, temps)` observed under `run/` — stays exactly as declared, and this is its own,
// separate, single-purpose pass.
//
// Unlike row 6, no state distinction is needed here. Row 6 needs nine states because a `run/` temp
// can coexist with several different token/record combinations that mean different things (mid-
// write vs. leftover-after-commit vs. stale-abandoned), and choosing the wrong repair for the wrong
// one is unsafe — that is what `expected`'s fingerprint check guards against. A chapter temp has no
// such ambiguity: this sweep is only ever invoked at recovery time (the same "before opening a new
// run" moment `recoverProvenanceState` is), when nothing is concurrently running
// `recordChapterProvenance` for any entry — so a temp found at rest IS a crash leftover, full stop,
// and removing it is unconditionally safe. That is also why this is one combined find-and-remove
// call rather than row 6's separate classify-then-repair split: there is no "wrong repair for this
// state" question to protect against by making the caller round-trip an `expected` value first.
//
// Entries-driven, like every other per-chapter call in this module (`recordChapterProvenance`,
// `buildProvenanceReport`) — never a raw directory walk of `chapters/`. This is a deliberate,
// narrower choice, not an oversight: a raw walk would need its OWN hazard discipline for every
// directory it descends into (any subdirectory could be a planted symlink), duplicating gate 6's
// hierarchy walk instead of reusing it, and — more fundamentally — this module already treats
// "which chapters exist" as manifest-derived everywhere else (`buildProvenanceReport` reports one
// row per `entries`, never one row per file found on disk), so a filesystem-driven exception here
// would be the one inconsistent reader of this state in the whole module. The consequence is
// explicit, not hidden: a chapter removed from the manifest between the crash and this call is not
// swept — operationally the same limitation `buildProvenanceReport` already has for a removed
// chapter's stale record.
// ---------------------------------------------------------------------------------------------

/**
 * Find and best-effort-remove every leftover `<slug>.json.<uuid>.tmp` chapter-record temp for each
 * of `entries` — the artifact left behind when a process dies between `recordChapterProvenance`
 * closing its temp and renaming it into place. Deliberately separate from `recoverProvenanceState`:
 * see the module comment above for why chapter temps are not folded into row 6's `(token, record,
 * temps)` tuple. Call this, like `recoverProvenanceState`, before opening a new run where a prior
 * chapter-write crash is suspected. Mutates nothing but the matched temps themselves — the run's
 * own token/record and every chapter's actual record are untouched.
 *
 * `removed` lists only the temps this call actually confirmed gone; a temp whose leaf inspection
 * passes (so it is safe to remove) but whose `unlinkSync` itself then fails (EACCES, a read-only
 * mount, ...) is reported in `warnings` instead — NEVER silently folded into `removed` (round 6,
 * finding 2: reporting an unremoved temp as removed is a false-clean this module has no other way
 * to catch, since row 6's classifier never observes `chapters/` at all, by design — see the module
 * banner above). This is a WARNING on an otherwise-`ok: true` result, not a halt: the temp is inert
 * litter either way (this call commits nothing; the record it is leftover FROM already committed or
 * failed independently, before this sweep ever ran), so one entry's stuck temp must not block every
 * other entry's genuinely-removable temps from being cleaned up, or the operator from proceeding.
 *
 * @param {object} profileLike
 * @param {Array<object>} entries
 * @param {object} [deps]
 * @returns {{ok: true, removed: string[], warnings: string[]}|{ok: true, skipped: true, removed: [], warnings: []}|{ok: false, halts: Array<object>}}
 */
export function sweepChapterProvenanceTemps(profileLike, entries, deps) {
  const d = mergeDeps(deps);
  const ownership = assertProvenanceOwnership(profileLike, d);
  if (ownership.skip) return { ok: true, skipped: true, removed: [], warnings: [] };
  if (!ownership.ok) return { ok: false, halts: ownership.halts };

  const removed = [];
  const warnings = [];
  for (const entry of entries) {
    // Gate 6's hierarchy walk over THIS entry's own chapters/(/<group>) ancestor chain — the same
    // check `recordChapterProvenance` runs before it ever opens this entry's leaf, so a symlinked
    // `chapters/` or group ancestor is refused here exactly as it would be on the write path,
    // rather than transparently followed by a sweep that skips the discipline the writer applies.
    const hierarchyHazard = inspectChaptersHierarchyComponents(profileLike, entry, d);
    if (hierarchyHazard) return { ok: false, halts: [{ halt: 'provenance_hazard', ...hierarchyHazard }] };

    const tempsListed = listMatchingChapterTemps(profileLike, entry, d);
    if (!tempsListed.ok) return { ok: false, halts: [{ halt: 'provenance_hazard', ...tempsListed.hazard }] };

    for (const temp of tempsListed.temps) {
      // Gate-6-verify the leaf (no-follow, regular, nlink===1) before touching it — the same
      // discipline row 6 applies to every `run/` temp it lists (`inspectTokenAndRecordAndTemps`),
      // so a symlink or hard-link planted at a temp's exact name halts here rather than being
      // unlinked blind.
      const tempLeaf = openLeafNoFollow(temp, fs.constants.O_RDONLY, d);
      if (tempLeaf.kind === 'hazard') return { ok: false, halts: [{ halt: 'provenance_hazard', ...tempLeaf }] };
      if (tempLeaf.kind === 'present') {
        closeBestEffort(tempLeaf.fd, d);
        // The leaf is already verified safe (no-follow, regular, single-linked) — a failure here is
        // an ordinary OS write-permission/mount failure, never the tampering hazard the check above
        // exists to catch, so it is reported rather than escalated to a halt (see the JSDoc above).
        if (unlinkBestEffort(temp, d)) {
          removed.push(temp);
        } else {
          warnings.push(
            `leftover chapter-record temp '${temp}' could not be removed and remains on disk — remove it manually, or re-run this sweep once the underlying issue (e.g. permissions) is resolved.`,
          );
        }
      }
      // 'absent': vanished between listing and open (e.g. a concurrent sweep already removed it) —
      // nothing to remove, not an error.
    }
  }
  return { ok: true, removed, warnings };
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
 * `identityCommandOutcome` is this function's counterpart of `openCaptureRun`'s parameter of the
 * same name — see that function's doc comment for why a UI-read continuation must reuse the
 * already-resolved outcome rather than re-invoking `capture.build_identity.command` a second time
 * for the same current-identity observation point.
 *
 * @param {object} profileLike
 * @param {Array<object>} entries
 * @param {import('./build-identity.mjs').UiReadObservation|null} [currentObservation]
 * @param {object} [deps]
 * @param {import('./build-identity.mjs').CommandOutcome|null} [identityCommandOutcome]
 * @returns {{rows: Array<object>}|{needs_ui_read: true, region_hint: string, identityCommandOutcome: import('./build-identity.mjs').CommandOutcome|null}|{ok: false, halts: Array<object>}}
 */
export function buildProvenanceReport(profileLike, entries, currentObservation, deps, identityCommandOutcome) {
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
        // [round 5, codex finding 4] Present and null, never absent. Every OTHER branch of this
        // function assigns `classifyBuildDelta`'s own `current_source`, so a row from THIS branch
        // that simply omitted the key made `ReportRow.current_source: string` a lie a TypeScript
        // caller could dereference and crash on — and nothing in this repository compiles
        // TypeScript, so no gate saw it. `null` rather than a string sentinel because a skipped
        // profile performs zero identity resolutions: there is no source, which is exactly what
        // the sibling `source: null` on this same row already says.
        current_source: null,
        // [round 16] And `record_detail` the same way, for the same reason, in the same object
        // literal — the comment above was written about `current_source` in round 5 and did not
        // stop the identical omission being made one field later. A skipped profile has no record
        // to describe, which is what `null` says; absent would make the declared `string | null`
        // a lie on the one branch a legacy profile always takes.
        record_detail: null,
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

  // Delegates to `resolveIdentityOrHalt` rather than calling `resolveIdentityCommandOutcome`/
  // `resolveBuildIdentity` directly: either can THROW (see that helper's own comment). W6 is the
  // audit entrypoint an operator runs over already-merged chapters, reachable from the same
  // UI-read observation this project's own reference doc classifies as untrusted, and a malformed
  // one previously escaped this function uncaught — measured against the real module (codex round
  // 9 follow-up). This function reads only; nothing is written before this point either way.
  const buildIdentity = profileLike.capture.build_identity ?? null;
  const resolvedCurrent = resolveIdentityOrHalt(identityCommandOutcome, buildIdentity, currentObservation, d);
  if (!resolvedCurrent.ok) return { ok: false, halts: [{ halt: resolvedCurrent.halt, message: resolvedCurrent.message }] };
  const { commandOutcome, identity: current } = resolvedCurrent;
  if (current.needs_ui_read) return { ...current, identityCommandOutcome: commandOutcome };

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
      return { ok: false, halts: [{ halt: 'chapter_read_failed', message: `cannot read chapter '${chapterFile}': ${describeThrownField(err, 'code')}`, key }] };
    }
    const assetDir = chapterAssetDir(profileLike, entry);
    let filenames;
    try {
      filenames = listRegularFilesRecursive(assetDir, d);
    } catch (err) {
      return { ok: false, halts: [{ halt: 'asset_listing_failed', message: `cannot list the asset directory for '${key}': ${describeThrownField(err, 'code')}`, key }] };
    }
    const extractionFn = deps?.expectedAssets ?? expectedAssets;
    let extraction;
    try {
      extraction = extractionFn(profileLike, entry, chapterFile, chapterText, filenames, profileLike.publish.target);
    } catch (err) {
      return { ok: false, halts: [{ halt: 'extraction_threw', message: describeThrown(err), key }] };
    }
    if (!extraction.ok) {
      return { ok: false, halts: [{ halt: 'extraction_halt', construct: extraction.halt.construct, line: extraction.halt.line, key }] };
    }

    let recordState;
    let staleDetail = null;
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
        // [round 14] The `if present` used to be the whole of it, and it made a hazardous embed
        // DISAPPEAR: `verifyRecord` compares only the keys it is handed, so an embed the chapter
        // still has but that gate 6 refuses to hash (a symlink, a directory, or an extra hard link
        // arriving after the record was written) was dropped from the comparison, and a chapter
        // with one good embed and one unhashable one reported `ok` and classified `unchanged`.
        // W6's promise is that every CURRENT embed is verified against the record; an embed whose
        // bytes cannot be read is neither verified nor absent, so it cannot be silently skipped.
        // It is `stale` — the same verdict as a differing hash, which is what "we cannot show this
        // is the recorded content" means — and never `ok`.
        const currentHashes = Object.create(null);
        const unhashable = [];
        for (const asset of extraction.assets) {
          const hashed = hashFileNoFollow(asset.absPath, d);
          if (hashed.kind === 'present') currentHashes[asset.key] = hashed.digest;
          else unhashable.push(`${asset.key}:${unreadableWord(hashed)}`);
        }
        if (unhashable.length > 0) {
          recordState = 'stale';
          // [round 15] Failing closed was right and losing the reason was not: byte-changed
          // content, a key missing from the record, a byte-identical file carrying an extra hard
          // link, and an outright read failure all produced the same `record_stale` row. The outer
          // verdict stays the same — the operator must not be told a chapter is verified — but
          // "the content changed" and "we could not read the content" call for different actions,
          // so the row carries which one it was.
          staleDetail = `unhashable:${unhashable.join(',')}`;
        } else {
          const verify = verifyRecord(chapterRecord.asset_hashes, currentHashes);
          recordState = verify.status === 'ok' ? 'ok' : 'stale';
          if (verify.status !== 'ok') {
            staleDetail = verify.path === undefined ? verify.reason : `${verify.reason}:${verify.path}`;
          }
        }
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
      // Present on every row, `null` when there is nothing to say — an absent key and "no detail"
      // are not the same reading, and a field that appears only sometimes gets treated as optional
      // by whatever renders it.
      record_detail: staleDetail,
    });
  }

  return { rows };
}

// ---------------------------------------------------------------------------------------------
// Row 6 — the nine-state classifier and its two repairs. Precedence, evaluated top to bottom:
// not_active -> orphan_temp -> absent -> partial -> malformed -> prepared -> open -> committed ->
// divergent. SKILL.md's W2 crash-recovery section is the authority this mirrors; the generated
// companion document this line used to cite never shipped (see the file header).
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
    return { ok: false, halts: [{ halt: 'mutation_failed', path, removed: removedSoFar, detail: describeThrown(err), currentState }] };
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
    if (errProp(err, 'code') !== 'ENOENT') {
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
