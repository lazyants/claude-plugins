// enduser-handbook — SKILL.md's spelled-out calls versus the real exported signatures.
//
// [round 12] SKILL.md is not documentation about the runtime; for a skill asset it IS the production
// caller. The model reads it and makes the call it describes. So a call written there with the wrong
// argument order is a production defect that no amount of testing the runtime can surface, and this
// release shipped one: the UI-read continuation was documented as passing `identityCommandOutcome`
// "as its next argument", while the runtime puts `deps` first. Following the instruction literally
// with default deps put the outcome in the `deps` slot and silently re-ran the operator's identity
// command — measured with a real command counting its own invocations, two per continuation pair
// instead of one. Every test stayed green, because every fixture passed an injected `deps` before the
// outcome and so did by accident what the prose never said.
//
// This suite closes that axis. It reads every `name(a, b, c)` SKILL.md spells out in backticks and
// checks the leading parameter names against the module's own `export function` signature. It cannot
// tell whether the prose around a call is correct — only that the call itself names the right
// parameters in the right order, which is exactly the property that was wrong.

import { test } from 'node:test';
import assert from 'node:assert';
import { readFileSync, readdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SKILL_DIR = join(HERE, '..', 'skills', 'enduser-handbook');
const LIB_DIR = join(SKILL_DIR, 'assets', 'lib');

/** Every `name(arg, arg)` written in backticks in SKILL.md, with its argument list as spelled. */
function citedCalls(markdown) {
  const calls = [];
  for (const m of markdown.matchAll(/`([a-zA-Z][A-Za-z0-9_]*)\(([^`)]*)\)`/g)) {
    const args = m[2].trim();
    if (args === '') continue; // a bare `foo()` mention makes no claim about arguments
    calls.push({ name: m[1], args });
  }
  return calls;
}

/** Every exported function's parameter names, per module, defaults and destructuring preserved. */
function exportedSignatures(libDir) {
  const signatures = new Map();
  for (const file of readdirSync(libDir).filter((f) => f.endsWith('.mjs'))) {
    const src = readFileSync(join(libDir, file), 'utf8');
    // [round 15] `export function` alone made the extractor silently FORGET any export written
    // another way: codex changed one to `export async function` and every check here stayed green
    // while the production repair had started returning a Promise. An extraction that narrows its
    // own population is the failure mode these pins exist to prevent, so the modifiers are matched
    // rather than assumed, and the required-entrypoint list below is the backstop that makes a
    // shrinking population fail instead of pass.
    for (const m of src.matchAll(/^export (?:async )?function\s*\*?\s*([A-Za-z0-9_]+)\(([^)]*)\)/gm)) {
      signatures.set(m[1], { file, params: splitParams(m[2]) });
    }
  }
  return signatures;
}

/** Split a parameter or argument list at top-level commas only, so `{a, b}` stays one item. */
function splitParams(text) {
  const out = [];
  let depth = 0;
  let current = '';
  for (const ch of text) {
    if (ch === '{' || ch === '[' || ch === '(') depth += 1;
    if (ch === '}' || ch === ']' || ch === ')') depth -= 1;
    if (ch === ',' && depth === 0) {
      if (current.trim()) out.push(current.trim());
      current = '';
      continue;
    }
    current += ch;
  }
  if (current.trim()) out.push(current.trim());
  return out;
}

/** A documented argument matches a real parameter when the names agree, or both are object-shaped. */
function argumentMatches(documented, real) {
  const realName = real.split('=')[0].trim();
  if (documented.startsWith('{') && realName.startsWith('{')) return true;
  return documented === realName;
}

const skill = readFileSync(join(SKILL_DIR, 'SKILL.md'), 'utf8');
const signatures = exportedSignatures(LIB_DIR);
const cited = citedCalls(skill).filter((c) => signatures.has(c.name));

test('SKILL.md spells out at least the provenance entrypoints — an empty check is not a passing one', () => {
  const names = new Set(cited.map((c) => c.name));
  for (const required of [
    'openCaptureRun',
    'closeCaptureRun',
    'recordChapterProvenance',
    'buildProvenanceReport',
    'recoverProvenanceState',
    'sweepChapterProvenanceTemps',
  ]) {
    assert.ok(names.has(required), `SKILL.md no longer spells out a call to ${required} — either the doc dropped it (a reachability defect) or this extraction broke`);
  }
});

// [round 13] SKILL.md was corrected in round 12, but the same wrong-slot rule survived as PROSE in
// two shipped assets: both stated that every exported function takes `deps` last. Three do not.
// A doc asserting a positional RULE is read by exactly the caller the round-12 defect misled, so it
// gets a gate of its own — one half measuring the real shape, one half forbidding the sentence that
// misstated it.
test('deps is last everywhere EXCEPT the three continuation entrypoints — measured from source', () => {
  const depsNotLast = [];
  for (const [name, { params }] of signatures) {
    const at = params.findIndex((p) => p.split('=')[0].trim() === 'deps');
    if (at !== -1 && at !== params.length - 1) depsNotLast.push(name);
  }
  assert.deepEqual(
    depsNotLast.sort(),
    ['buildProvenanceReport', 'closeCaptureRun', 'openCaptureRun'],
    'the set of exports where `deps` is not the final parameter changed — every doc stating a position for `deps` must be re-read against the new shape',
  );
});

// [round 14] Round 13 replaced the false "takes `deps` last" with "every exported function accepts
// `deps`", which is false too — seven exports are pure and take none. The replacement inherited the
// shape of what it replaced, which is why the set is pinned here rather than asserted in prose: a
// sentence naming a set has to be re-read by a human every time the set changes, and this does not.
// Scoped to capture-record.mjs, because that is the module the seam comment belongs to — the other
// modules in `lib/` are pure by design and take no `deps` at all, so folding them in would say
// nothing about the sentence under test.
test('exactly the disk-touching exports accept deps — measured from source', () => {
  const withDeps = [];
  const withoutDeps = [];
  for (const [name, { file, params }] of signatures) {
    if (file !== 'capture-record.mjs') continue;
    (params.some((p) => p.split('=')[0].trim() === 'deps') ? withDeps : withoutDeps).push(name);
  }
  assert.deepEqual(
    withoutDeps.sort(),
    ['chapterRecordPath', 'digestOpeningPayload', 'jcsCanonicalize', 'provenanceRoot',
      'readChapterRecordText', 'readRunRecordText', 'sha256HexOfCanonical'],
    'the set of exports taking NO `deps` changed — any prose describing the seam as covering "every exported function" has to be re-read against it',
  );
  // [round 15] `length > 0` was the whole of this side, and it is the weak half of the pin: an
  // export that the extractor silently stops recognizing simply leaves the list, and a count that
  // only has to beat zero never notices. Naming them means a forgotten export fails here rather
  // than in whatever the extraction fed next.
  assert.deepEqual(
    withDeps.sort(),
    ['abortCaptureRun', 'assertProvenanceOwnership', 'buildProvenanceReport', 'cleanupCommittedRun',
      'closeCaptureRun', 'openCaptureRun', 'recordChapterProvenance', 'recoverProvenanceState',
      'sweepChapterProvenanceTemps'],
    'the set of capture-record exports accepting `deps` changed — if this shrank, check first whether the extractor stopped recognizing an export rather than whether the export went away',
  );
});

test('no shipped asset states that deps is the last argument — it is not, for three of them', () => {
  const offenders = [];
  for (const file of readdirSync(LIB_DIR).filter((f) => f.endsWith('.mjs') || f.endsWith('.d.mts'))) {
    const src = readFileSync(join(LIB_DIR, file), 'utf8');
    // Collapse whitespace: the claim wrapped across a line break in both places it was found, and a
    // line-oriented search would have matched neither.
    const flat = src.replace(/\s+/g, ' ');
    for (const claim of ['takes `deps` last', 'accepts as its last argument', '`deps` as its last argument']) {
      if (flat.includes(claim)) offenders.push(`${file}: "${claim}"`);
    }
  }
  assert.deepEqual(offenders, [], `a shipped asset states a false position for \`deps\`:\n  ${offenders.join('\n  ')}`);
});

test('every call SKILL.md spells out names the real parameters, in the real order', () => {
  const wrong = [];
  for (const { name, args } of cited) {
    const { file, params } = signatures.get(name);
    const documented = splitParams(args);
    if (documented.length > params.length) {
      wrong.push(`${name}: SKILL.md passes ${documented.length} arguments, ${file} accepts ${params.length}`);
      continue;
    }
    documented.forEach((arg, i) => {
      if (!argumentMatches(arg, params[i])) {
        wrong.push(`${name}: SKILL.md's argument ${i + 1} is '${arg}', but ${file} declares '${params[i]}' there — an operator following this prose passes it into the wrong slot`);
      }
    });
  }
  assert.deepEqual(wrong, [], `SKILL.md describes a call the runtime does not accept:\n  ${wrong.join('\n  ')}`);
});
