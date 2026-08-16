// enduser-handbook — the declaration/runtime parity GATE over the shipped assets/lib (#420, #339).
//
// #420: nothing in this repository compiles TypeScript, so a hand-maintained `.d.mts` can contradict
// the `.mjs` beside it and the entire suite stays green. The 1.12.0 release produced several such
// corrections, every one caught by a human reading the two files side by side.
//
// This is #420's option 2 — a structural check that every exported symbol has a declaration and vice
// versa — widened into the general gate #339 asks for, and extended with the one runtime fact a
// structural check can still reach without a compiler: ARITY. A declaration that lost a parameter is
// the shape the 1.12.0 corrections kept taking, and `Function.prototype.length` measures it for free.
//
// #420's preferred option 1 (`tsc --noEmit` over the declarations plus a conformance file) is NOT
// implemented here and was not silently substituted for. It is not buildable in this repository as it
// stands: there is no `package.json`, no `tsconfig`, no dependency manifest and no CI anywhere in the
// tree, and the `.mjs` files carry only scattered JSDoc, so there is no machine-readable runtime type
// source for a compiler to check the declarations against. Adding a TypeScript toolchain is a
// standing decision for the repository owner, not something a test file gets to make. What option 1
// would still catch and this gate does not: a declared TYPE that is wrong while the name and the
// parameter count are both right.
//
// The gate is green on the tree it shipped against, which proves nothing on its own — so every
// direction it claims to catch is exercised below against a deliberately drifted fixture and asserted
// RED, and the clean-tree case is asserted GREEN in the same file. A guard is only as good as its
// two-sided mutation probe.

import { test } from 'node:test';
import assert from 'node:assert';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

import { auditLibDirectory } from './export-parity-lib.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const LIB_DIR = join(HERE, '..', 'skills', 'enduser-handbook', 'assets', 'lib');

/**
 * Build a throwaway `assets/lib`-shaped directory and audit it.
 *
 * Every fixture gets its own `mkdtemp` directory, so each `.mjs` is imported under a URL the module
 * loader has never seen — a shared fixture path would be served from the loader's cache and the
 * second mutation of a file would silently audit the first one's exports.
 */
async function auditFixture(files) {
  const dir = mkdtempSync(join(tmpdir(), 'eh-declaration-parity-'));
  try {
    for (const [name, contents] of Object.entries(files)) writeFileSync(join(dir, name), contents);
    return await auditLibDirectory(dir);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

/** Assert that at least one finding mentions each of the given fragments, and say what was found. */
function assertFinding(result, ...fragments) {
  for (const fragment of fragments) {
    assert.ok(
      result.findings.some((f) => f.includes(fragment)),
      `expected a finding mentioning '${fragment}'; got:\n  ${result.findings.join('\n  ') || '(no findings at all — the gate stayed green on a drifted fixture)'}`,
    );
  }
}

// ---------------------------------------------------------------------------------------------
// The gate itself, over the real shipped tree.
// ---------------------------------------------------------------------------------------------

const live = await auditLibDirectory(LIB_DIR);

test('every shipped assets/lib module agrees with its own .d.mts, both directions and on arity', () => {
  assert.deepEqual(
    live.findings,
    [],
    `assets/lib has drifted from its declarations:\n  ${live.findings.join('\n  ')}`,
  );
});

test('the parity run enumerated a plausible tree — an empty audit is not a passing one', () => {
  // A run that enumerated nothing produces an empty finding list, which is byte-for-byte what a clean
  // tree produces. These are the assertions that tell the two apart.
  assert.ok(live.census.modules > 0, 'the parity audit enumerated ZERO modules — it proved nothing, it did not pass');
  assert.ok(
    live.census.modules >= 12,
    `the parity audit enumerated only ${live.census.modules} module pairs; 12 shipped when this gate was written. A module was deliberately removed (lower this floor in the same commit) or the enumeration silently half-matched.`,
  );
  assert.ok(
    live.census.runtimeExports >= 89,
    `the parity audit compared only ${live.census.runtimeExports} runtime exports; 89 shipped when this gate was written`,
  );
  assert.ok(
    live.census.arityChecks >= 80,
    `the parity audit compared arity for only ${live.census.arityChecks} functions; 80 shipped when this gate was written`,
  );
  // Three separate review findings were the same shape — a function export whose declared parameter
  // list the extractor could not read, skipped without a word. Each was fixed by teaching it one more
  // spelling, which only shortens the list of spellings nobody has thought of yet. This is what
  // closes the class: the runtime says these exports are functions, so an arity that was never
  // compared is named rather than absorbed into a total that still looks healthy.
  assert.deepEqual(
    live.arityUnread,
    [],
    `a function export's declared arity was never compared, and a skipped comparison is indistinguishable from a passing one:\n  ${live.arityUnread.join('\n  ')}\nEither teach functionTypeArity this declaration's spelling, or — if the type genuinely needs a compiler to resolve — spell the signature inline at the declaration so it can be read here.`,
  );

  // Per module, not just in aggregate: one module falling silent is invisible in a total that another
  // module's exports keep above the floor.
  const silent = live.modules.filter((m) => m.runtimeExports === 0 || m.valueDeclarations === 0);
  assert.deepEqual(
    silent.map((m) => m.base),
    [],
    `a module contributed nothing to the comparison, so nothing about it was checked: ${silent.map((m) => `${m.base} (runtime ${m.runtimeExports}, declared ${m.valueDeclarations})`).join(', ')}`,
  );
});

test('the module census is printed, so a shrinking population is visible and not merely assertable', () => {
  const line = `assets/lib parity: ${live.census.modules} module pairs, ${live.census.runtimeExports} runtime exports, ${live.census.valueDeclarations} value declarations, ${live.census.typeDeclarations} type declarations, ${live.census.arityChecks} arity comparisons`;
  console.log(line);
  assert.match(line, /\d+ module pairs/);
});

// ---------------------------------------------------------------------------------------------
// Two-sided mutation probes. Each drifts one thing and asserts the gate goes RED for that reason.
// ---------------------------------------------------------------------------------------------

const CLEAN_MJS = 'export function alpha(a, b) { return [a, b]; }\nexport const BETA = 1;\n';
const CLEAN_DTS = 'export declare function alpha(a: unknown, b: unknown): unknown[];\nexport declare const BETA: 1;\n';

test('control: an undrifted fixture pair is GREEN, so every red below is the mutation and not the harness', async () => {
  const result = await auditFixture({ 'm.mjs': CLEAN_MJS, 'm.d.mts': CLEAN_DTS });
  assert.deepEqual(result.findings, [], `the control fixture should be clean:\n  ${result.findings.join('\n  ')}`);
  assert.equal(result.census.modules, 1);
  assert.equal(result.census.runtimeExports, 2);
  assert.equal(result.census.arityChecks, 1);
});

test('RED: a declaration deleted while the function still ships', async () => {
  const result = await auditFixture({
    'm.mjs': CLEAN_MJS,
    'm.d.mts': 'export declare const BETA: 1;\n',
  });
  assertFinding(result, 'runtime exports `alpha` with no value declaration');
});

test('RED: a new .mjs export shipped with no declaration at all', async () => {
  const result = await auditFixture({
    'm.mjs': `${CLEAN_MJS}export function brandNewUndeclaredExport(x) { return x; }\n`,
    'm.d.mts': CLEAN_DTS,
  });
  assertFinding(result, 'runtime exports `brandNewUndeclaredExport` with no value declaration');
});

test('RED: a declaration for a name the runtime does not export', async () => {
  const result = await auditFixture({
    'm.mjs': CLEAN_MJS,
    'm.d.mts': `${CLEAN_DTS}export declare function ghost(x: unknown): void;\n`,
  });
  assertFinding(result, 'declares `ghost`', 'but the runtime does not export it');
});

test('RED: the runtime grew a parameter the declaration never heard about', async () => {
  const result = await auditFixture({
    'm.mjs': 'export function alpha(a, b, c) { return [a, b, c]; }\nexport const BETA = 1;\n',
    'm.d.mts': CLEAN_DTS,
  });
  assertFinding(result, '`alpha` takes 3 parameter(s) at runtime, but m.d.mts declares 2');
});

test('RED: the runtime lost a parameter the declaration still requires', async () => {
  const result = await auditFixture({
    'm.mjs': 'export function alpha(a) { return [a]; }\nexport const BETA = 1;\n',
    'm.d.mts': CLEAN_DTS,
  });
  assertFinding(result, '`alpha` takes 1 parameter(s) at runtime, but m.d.mts declares 2');
});

test('GREEN: an optional parameter admits BOTH the defaulted and the plain runtime spelling', async () => {
  // TypeScript cannot carry an initializer in an ambient declaration, so `b?: T` is how a runtime
  // `b = 1` is spelled — and `Function.prototype.length` stops counting AT the default. Asserting a
  // single exact number here would fail one of the two legitimate spellings, so the declared arity is
  // a range. Both spellings must stay green or the gate is a false-red generator.
  const declaration = 'export declare function alpha(a: unknown, b?: unknown): void;\n';
  const defaulted = await auditFixture({ 'm.mjs': 'export function alpha(a, b = 1) { return [a, b]; }\n', 'm.d.mts': declaration });
  assert.deepEqual(defaulted.findings, [], `a defaulted parameter must satisfy an optional declaration:\n  ${defaulted.findings.join('\n  ')}`);
  const plain = await auditFixture({ 'm.mjs': 'export function alpha(a, b) { return [a, b]; }\n', 'm.d.mts': declaration });
  assert.deepEqual(plain.findings, [], `a plain parameter must satisfy an optional declaration:\n  ${plain.findings.join('\n  ')}`);
  // …and the range still has a floor: dropping BOTH parameters is out of it.
  const dropped = await auditFixture({ 'm.mjs': 'export function alpha() { return []; }\n', 'm.d.mts': declaration });
  assertFinding(dropped, '`alpha` takes 0 parameter(s) at runtime, but m.d.mts declares 1-2');
});

test('RED: a function export whose declared arity could not be read is NAMED, never quietly skipped', async () => {
  // Not a finding — a declaration may legitimately name a type only a compiler could resolve — but
  // never silent either. A comparison that did not happen is indistinguishable from one that passed,
  // and three separate review findings in this gate were exactly that shape.
  const result = await auditFixture({
    'm.mjs': 'export const handler = (a, b) => [a, b];\nexport function plain(a) { return a; }\n',
    'm.d.mts': 'export interface Callback { (a: number): void }\nexport declare const handler: Callback;\nexport declare function plain(a: number): void;\n',
  });
  assert.deepEqual(result.findings, [], `an unreadable arity is not itself a defect:\n  ${result.findings.join('\n  ')}`);
  assert.deepEqual(result.arityUnread, ['m: handler (const, line 2)'], 'the export whose arity went uncompared must be named');
  assert.equal(result.census.arityUnread, 1);
  // The readable sibling must still have been compared, or "unread" would just mean "gave up".
  assert.equal(result.census.arityChecks, 1);
});

test('RED: a stale declaration-only module — the direction a .mjs walk cannot see', async () => {
  const result = await auditFixture({
    'm.mjs': CLEAN_MJS,
    'm.d.mts': CLEAN_DTS,
    'zombie.d.mts': 'export declare function gone(): void;\n',
  });
  assertFinding(result, 'zombie.d.mts is a stale declaration-only module');
});

test('RED: a runtime module shipped with no declaration file beside it', async () => {
  const result = await auditFixture({
    'm.mjs': CLEAN_MJS,
    'm.d.mts': CLEAN_DTS,
    'orphan.mjs': 'export function lonely() {}\n',
  });
  assertFinding(result, 'orphan.mjs ships with no orphan.d.mts beside it');
});

test('RED: an interface does not stand in for a value — the two declaration spaces are not one', async () => {
  // `export interface alpha {}` declares a TYPE named alpha. A name-only comparison that ignored the
  // declaration space would read this as satisfying the runtime export and stay green forever.
  const result = await auditFixture({
    'm.mjs': CLEAN_MJS,
    'm.d.mts': 'export interface alpha { a: unknown }\nexport declare const BETA: 1;\n',
  });
  assertFinding(result, 'runtime exports `alpha` with no value declaration', 'only in TYPE space');
});

test('RED: a declaration file this extractor cannot read fails the gate rather than skipping it', async () => {
  const result = await auditFixture({
    'm.mjs': CLEAN_MJS,
    'm.d.mts': 'export * from "./somewhere-else.mjs";\n',
  });
  assertFinding(result, 'bare `export * from`');
});
