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
    for (const m of src.matchAll(/^export function ([A-Za-z0-9_]+)\(([^)]*)\)/gm)) {
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
