// enduser-handbook — the statement-aware extractor behind the export-parity gate (#339).
//
// #339 is not "write a parity check". It is the record of five consecutive rounds in which a
// regex/allowlist parity check produced a MEASURED false-green, each escalation proposed in answer to
// the previous round's hole and each growing a new one. The issue's own instruction to whoever picked
// it up was to start from "what would a minimal statement-aware extractor need to parse" rather than
// from "what regex have we not tried yet", and to keep every measured defeat so the next attempt does
// not re-earn them one round at a time.
//
// So this file is that inventory, turned into fixtures. Every case below is one of the enumerated
// defeats, written as the smallest source that reproduces it, with the answer the extractor must
// give. They are deliberately shapes the shipped tree does NOT contain — the tree is flat, unindented
// and single-statement-per-line, which is exactly why a regex passed against it for five rounds while
// being wrong. A gate verified only against the tree it ships with is verified against the one input
// guaranteed not to exercise it.
//
// Two cases are re-derived here rather than quoted from the issue, because the issue says to: the
// census magnitudes it records were measured at the 1.10.0 tree and are a snapshot, not a property.
// What is durable is the SHAPE of each defeat.

import { test } from 'node:test';
import assert from 'node:assert';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { readdir, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  maskInert,
  extractDeclarationExports,
  declaredArity,
  loadRuntimeExports,
  auditLibDirectory,
} from './export-parity-lib.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const LIB_DIR = join(HERE, '..', 'skills', 'enduser-handbook', 'assets', 'lib');

/** The value-space names a source declares, sorted — the comparison the parity gate actually makes. */
function valueNames(source) {
  return [...extractDeclarationExports(source).values.keys()].sort();
}

/** The type-space names a source declares, sorted. */
function typeNames(source) {
  return [...extractDeclarationExports(source).types].sort();
}

async function withFixture(files, fn) {
  const dir = mkdtempSync(join(tmpdir(), 'eh-export-parity-'));
  try {
    for (const [name, contents] of Object.entries(files)) writeFileSync(join(dir, name), contents);
    return await fn(dir);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

// ---------------------------------------------------------------------------------------------
// The measured false-green inventory from #339, each as the smallest source that reproduces it.
// ---------------------------------------------------------------------------------------------

test('#339: both function spellings are read — a fixed prefix reads only one of them', () => {
  // The declaration side carries `export function` AND `export declare function`; the source side
  // carries `export function` AND `export async function`. A census anchored on one spelling silently
  // narrows its own population, and the names it never enumerated cannot fail any comparison.
  assert.deepEqual(
    valueNames([
      'export function plain(a: A): void;',
      'export declare function ambient(a: A): void;',
      'export async function eager(a: A): Promise<void>;',
    ].join('\n')),
    ['ambient', 'eager', 'plain'],
  );
});

test('#339: an INDENTED export is still an export — `^export` is blind to it', () => {
  assert.deepEqual(
    valueNames('  export function Ghost(): void;\n\texport declare const TABBED: 1;\n'),
    ['Ghost', 'TABBED'],
  );
});

test('#339: a declarator list declares EVERY binding, not just the first', () => {
  assert.deepEqual(valueNames('export const A = 1, ghost = 2;'), ['A', 'ghost']);
  // The TypeScript-only spelling, found in round 24 — a type annotation, no initializer.
  assert.deepEqual(valueNames('export declare const A: 1, ghost: 2;'), ['A', 'ghost']);
});

test('#339: a declarator list split across several lines still declares every binding', () => {
  assert.deepEqual(
    valueNames([
      'export declare const',
      '  A: 1,',
      '  ghost: 2,',
      '  third: 3;',
    ].join('\n')),
    ['A', 'ghost', 'third'],
  );
});

test('#339: a comma inside a type annotation does NOT split a declarator', () => {
  // The other half of the declarator-list problem, and the one a naive comma split gets wrong in the
  // opposite direction: `Record<number, T>` and `{ a: 1, b: 2 }` each carry a comma that belongs to
  // the type, not to the list. Both shapes are live in the shipped tree.
  assert.deepEqual(
    valueNames('export declare const MIGRATIONS: Record<number, { to: number; instructions: string }>;'),
    ['MIGRATIONS'],
  );
  assert.deepEqual(
    valueNames('export declare const PAIR: { a: 1, b: 2 }, second: 3;'),
    ['PAIR', 'second'],
  );
});

test('#339: two separate export statements on ONE physical line are both read', () => {
  assert.deepEqual(
    valueNames('export declare const A: 1; export declare function b(): void;'),
    ['A', 'b'],
  );
});

test('#339: interface and type declarations are TYPE space — never false reds, never value stand-ins', () => {
  const source = [
    'export interface Shape { a: number }',
    'export type Alias = Shape | null;',
    'export declare function make(): Shape;',
  ].join('\n');
  assert.deepEqual(valueNames(source), ['make'], 'an interface or type alias has no runtime existence and must not be demanded of the namespace');
  assert.deepEqual(typeNames(source), ['Alias', 'Shape']);
});

// ---------------------------------------------------------------------------------------------
// Where the text SITS, which is the property every regex round kept getting wrong.
// ---------------------------------------------------------------------------------------------

test('an `export` inside a comment, a string or a template declares nothing', () => {
  const source = [
    '// export function fromLineComment(): void;',
    '/* export function fromBlockComment(): void; */',
    '/**',
    ' * Prose that spells `export declare const FROM_DOC: 1;` inside a doc comment.',
    ' */',
    'export declare const REAL: 1;',
    'export type Spelled = "export function fromString(): void";',
    'export type Templated = `export declare const FROM_TEMPLATE: ${string}`;',
  ].join('\n');
  assert.deepEqual(valueNames(source), ['REAL']);
  assert.deepEqual(typeNames(source), ['Spelled', 'Templated']);
});

test('an `export` nested inside a declaration BODY is not a module export', () => {
  // Depth, not indentation, is what makes a member a member — a property may legally be named
  // `export`, and a nested block is not the module surface.
  const source = [
    'export interface Holder {',
    '  export: number;',
    '  nested: { export: string };',
    '}',
    'export declare const REAL: 1;',
  ].join('\n');
  assert.deepEqual(valueNames(source), ['REAL']);
});

test('a substring of a longer identifier is not the `export` keyword', () => {
  assert.deepEqual(valueNames('export declare const exported_thing: 1;\nexport declare const reexport: 2;'), ['exported_thing', 'reexport']);
});

test('maskInert preserves offsets and line structure, so a name slices out of the original', () => {
  const source = 'const a = "hidden";\n// note\nexport declare const B: 1;\n';
  const masked = maskInert(source);
  assert.equal(masked.length, source.length, 'masking must not move a single byte');
  assert.equal(masked.split('\n').length, source.split('\n').length, 'masking must not lose a line break');
  assert.ok(!masked.includes('hidden'), 'string contents must be blanked');
  assert.ok(!masked.includes('note'), 'comment contents must be blanked');
  assert.ok(masked.includes('export declare const B'), 'live code must survive untouched');
});

test('maskInert holds its offset invariant across a NON-ASCII character, not just an ASCII one', () => {
  // The invariant above was asserted only over ASCII, and that is how it came to be false: the mask
  // was built with `Array.from`, which splits by code POINT, while every read and every offset it
  // hands back index by UTF-16 code UNIT. One emoji in a doc comment shortened the array and shifted
  // the mask by one for the whole rest of the file — no throw, no visible symptom, just offsets that
  // quietly stopped meaning what the caller thought. An invariant test that only ever sees the
  // characters the bug cannot reach is not testing the invariant.
  const astral = String.fromCodePoint(0x1F600);
  const combining = 'é';                       // a combining mark: two units, one grapheme
  const bmpNonAscii = '—«»';                          // multi-byte in UTF-8 but single UTF-16 units
  for (const marker of [astral, combining, bmpNonAscii]) {
    const source = `/** ${marker} */\nexport declare const AFTER: 1;\n`;
    const masked = maskInert(source);
    assert.equal(
      masked.length,
      source.length,
      `masking moved an offset around ${JSON.stringify(marker)} — every position after it now points at the wrong character`,
    );
    assert.equal(
      masked.indexOf('export declare const AFTER'),
      source.indexOf('export declare const AFTER'),
      `the export's offset shifted around ${JSON.stringify(marker)}, so a name sliced from the original would come out wrong`,
    );
    assert.deepEqual([...extractDeclarationExports(source).values.keys()], ['AFTER']);
  }
});

test('an unterminated comment, string or template is a hard failure, never a blank-to-end-of-file', () => {
  // Blanking the tail of a file is the failure that reads green while checking nothing: everything
  // after the unterminated construct simply stops existing, and an empty surface raises no finding by
  // itself.
  assert.throws(() => maskInert('/* never closed\nexport declare const A: 1;'), /unterminated block comment/);
  assert.throws(() => maskInert("const s = 'never closed\nexport declare const A: 1;"), /unterminated .*string/);
  assert.throws(() => maskInert('const t = `never closed\nexport declare const A: 1;'), /unterminated template/);
});

// ---------------------------------------------------------------------------------------------
// Fail-closed: a shape the extractor cannot read is reported, never skipped.
// ---------------------------------------------------------------------------------------------

test('every construct the extractor cannot read is REPORTED rather than silently skipped', () => {
  const cases = [
    ['export * from "./other.mjs";', 'bare `export * from`'],
    ['export = Something;', 'unrecognized export construct'],
    ['declare namespace Wrapper { export const A: 1; }', 'ambient `declare namespace` block'],
    ['declare module "x" { export const A: 1; }', 'ambient `declare module` block'],
    ['declare global { interface Window { a: 1 } }', 'ambient `declare global` block'],
    ['export declare const { destructured }: { destructured: 1 };', 'is not a plain binding name'],
  ];
  for (const [source, fragment] of cases) {
    const { unsupported } = extractDeclarationExports(source, 'fixture.d.mts');
    assert.ok(
      unsupported.some((u) => u.includes(fragment)),
      `'${source}' should have been reported as unsupported (looking for '${fragment}'); got ${JSON.stringify(unsupported)}`,
    );
  }
});

test('unbalanced brackets are REPORTED — the depth counter cannot be trusted silently', () => {
  // The whole extractor trusts bracket depth to decide which `export` is a module export, which makes
  // the counter itself the one thing that must not fail quietly. An unclosed brace pins depth above
  // zero for the rest of the file, so every later top-level export is skipped: no finding, no name
  // compared, and an empty surface handed back as if the file simply exported nothing.
  const hidden = 'export interface Broken { a: number\nexport declare const HIDDEN: 1;\n';
  const { values, unsupported } = extractDeclarationExports(hidden, 'broken.d.mts');
  assert.deepEqual([...values.keys()], [], 'the unclosed brace does hide the later export — that is the hazard');
  assert.ok(
    unsupported.some((u) => u.includes('brackets did not pair up')),
    `the unclosed bracket must be named; got ${JSON.stringify(unsupported)}`,
  );
  // Both layers fire here, and that is the design rather than redundancy: the bracket check says the
  // FILE is broken, the nested-export check says which DECLARATION was lost by it.
  assert.ok(
    unsupported.some((u) => u.includes('HIDDEN') && u.includes('sits inside a nested block')),
    `the hidden export must be named, not merely implied by a bracket complaint; got ${JSON.stringify(unsupported)}`,
  );
  // A stray closer is the mirror image: it accounts for no opener, so the structure either side of
  // it is wrong even when the count happens to end back at zero.
  const stray = extractDeclarationExports('export declare const A: 1;\n\n}\nexport declare const B: 2;\n', 'stray.d.mts');
  assert.ok(
    stray.unsupported.some((u) => u.includes('first unmatched closer at stray.d.mts:3')),
    `a closer with no opener must be reported AT ITS OWN LINE — an end-of-file report is the least actionable form of the same fact; got ${JSON.stringify(stray.unsupported)}`,
  );
});

test('a stray opener and closer STRADDLING a declaration cannot hide it — balance alone misses this', () => {
  // Bracket balance is necessary but not sufficient, and this is the shape that proves it: a stray
  // `{` before a declaration and a stray `}` after it leave depth ending at zero, never going
  // negative. The balance check passes; the declaration between them sits at depth 1 and is skipped.
  // Nothing reports it, and — the part that makes it a false green rather than a wrong answer — a
  // name that never enters the extracted set is a name that no comparison in either direction can
  // miss, so a genuinely stale `ghost` declaration would be invisible to the whole gate.
  const straddled = [
    'export declare function realA(): void; {',
    'export declare function ghost(x: number): void; }',
    'export declare function realB(): void;',
  ].join('\n');
  const { values, unsupported } = extractDeclarationExports(straddled, 'm.d.mts');
  assert.deepEqual([...values.keys()], ['realA', 'realB'], 'the hidden declaration is genuinely not extracted — that is the hazard');
  assert.ok(
    unsupported.some((u) => u.includes('sits inside a nested block') && u.includes('m.d.mts:2')),
    `the straddled declaration must be reported, at its own line; got ${JSON.stringify(unsupported)}`,
  );

  // EVERY export spelling, not the ones an allowlist remembered. The first revision of this check
  // listed the declaration heads that count as a hidden export, and each spelling missing from that
  // list passed silently — an allowlist of what is forbidden fails green on everything it forgot.
  for (const hidden of [
    'export declare function ghost(x: number): void;',
    'export { ghost };',
    'export { ghost } from "./x.mjs";',
    'export * as ghost from "./x.mjs";',
    'export default ghost;',
    'export namespace ghost {}',
    'export declare class Ghost {}',
    'export declare enum GhostEnum {}',
    'export type Ghost = 1;',
    'export = ghost;',
  ]) {
    const src = `export declare const before: 1; {\n${hidden} }\nexport declare const after: 2;\n`;
    const r = extractDeclarationExports(src, 'h.d.mts');
    assert.ok(
      r.unsupported.some((u) => u.includes('sits inside a nested block')),
      `a nested \`${hidden}\` must be refused, not skipped; got ${JSON.stringify(r.unsupported)}`,
    );
  }

  // The other side of the discriminator: every shape of member merely NAMED `export` must stay
  // silent, or the check is a false-red generator over ordinary bodies.
  for (const member of [
    'export: number;',
    'export?: string;',
    'export(): void;',
    'export<T>(t: T): void;',
    'export?(): void;',
    'readonly export: number;',
    'export: (a: number) => void;',
    'export: { const: number };',
  ]) {
    const src = `export interface Holder {\n  ${member}\n}\nexport declare const REAL: 1;\n`;
    const r = extractDeclarationExports(src, 'holder.d.mts');
    assert.deepEqual(r.unsupported, [], `a member named \`export\` (${member}) is not a hidden declaration`);
    assert.deepEqual([...r.values.keys()], ['REAL']);
  }
});

test('bracket KINDS are matched, not just counted — a count-balanced file can still be invalid', () => {
  // `[number)` counts as one opener and one closer, so a bare depth counter finishes at zero and
  // calls the file reliable. It is not valid TypeScript, and with no compile step anywhere in this
  // repository this gate is the only thing that would ever say so.
  const mismatched = 'export interface Broken { field: [number) }\nexport declare const OK: 1;\n';
  const { unsupported } = extractDeclarationExports(mismatched, 'b.d.mts');
  assert.ok(
    unsupported.some((u) => u.includes('brackets did not pair up') && u.includes('mismatched closer at b.d.mts:1')),
    `a mismatched bracket pair must be reported at its own line; got ${JSON.stringify(unsupported)}`,
  );
  // …and a correctly bracketed file with all three kinds nested must stay silent.
  const fine = 'export declare function f(a: Array<[number, { b: string }]>): void;\n';
  assert.deepEqual(extractDeclarationExports(fine, 'ok.d.mts').unsupported, []);
});

test('export lists and namespace re-exports resolve to the EXPORTED name, not the local one', () => {
  const source = [
    'declare const local: 1;',
    'export { local as public, plain };',
    'export type { Shape, Other as Renamed };',
    'export { type Inline, value } from "./m.mjs";',
    'export * as bundled from "./m.mjs";',
  ].join('\n');
  assert.deepEqual(valueNames(source), ['bundled', 'plain', 'public', 'value']);
  // `Other` is deliberately absent: it is the LOCAL name of the type re-exported as `Renamed`, and a
  // comparison keyed on the local name would demand a runtime export nothing declares.
  assert.deepEqual(typeNames(source), ['Inline', 'Renamed', 'Shape']);
});

test('a default export is keyed `default`, whatever shape it takes', () => {
  assert.deepEqual(valueNames('export default function named(a: A): void;'), ['default']);
  assert.deepEqual(valueNames('export default Something;'), ['default']);
  // An object literal opens with a brace that is NOT an export specifier list.
  assert.deepEqual(valueNames('export default { a: 1 };'), ['default']);
});

// ---------------------------------------------------------------------------------------------
// Declared arity — the runtime fact a structural check can still reach without a compiler (#420).
// ---------------------------------------------------------------------------------------------

test('declared arity is a RANGE, because Function.prototype.length does not count optionality', () => {
  assert.deepEqual(declaredArity('a: A, b: B'), { min: 2, max: 2 });
  assert.deepEqual(declaredArity('a: A, b?: B'), { min: 1, max: 2 });
  assert.deepEqual(declaredArity(''), { min: 0, max: 0 });
  assert.deepEqual(declaredArity('a: A, ...rest: B[]'), { min: 1, max: Infinity });
});

test('declared arity is not fooled by commas, arrows or a `this` annotation', () => {
  assert.deepEqual(declaredArity('map: Map<string, number>, list: Array<[A, B]>'), { min: 2, max: 2 });
  // `=>` carries a `>` that closes nothing and an `=` that is not a default — both were trapdoors.
  assert.deepEqual(declaredArity('cb: (a: A, b: B) => void, after: C'), { min: 2, max: 2 });
  assert.deepEqual(declaredArity('cb: <T extends (x: A) => B>(t: T) => void'), { min: 1, max: 1 });
  // A `this` parameter is TypeScript's callee annotation and occupies no runtime slot.
  assert.deepEqual(declaredArity('this: Host, a: A'), { min: 1, max: 1 });
});

test('the ONE shape this arity check can be wrong about is named in its own failure message', async () => {
  // Measured, not assumed: of seven candidate false-RED pairs, exactly two land — a runtime that
  // takes its arguments through a rest parameter, and one that reads `arguments`. Both report
  // `length === 0` while the declaration legitimately spells real parameters. A fully destructured
  // signature, a declared rest, a const of function type, a bound function and an overload union all
  // stay green, so the hole is exactly this one and it is not general.
  //
  // Left as a false RED on purpose. It fails LOUD and the message says what to do, whereas every way
  // of suppressing it requires deciding from the outside which zero is a real zero — and being wrong
  // in THAT direction is silent, which is the trade this whole gate exists to refuse. No shipped
  // module takes arguments either way today.
  for (const runtime of ['export function f(...args) { return args; }', 'export function f() { return arguments[0]; }']) {
    const dir = mkdtempSync(join(tmpdir(), 'eh-arity-zero-'));
    try {
      writeFileSync(join(dir, 'm.mjs'), `${runtime}\n`);
      writeFileSync(join(dir, 'm.d.mts'), 'export declare function f(a: A, b: B): void;\n');
      const { findings } = await auditLibDirectory(dir);
      const arity = findings.filter((x) => x.includes('at runtime, but'));
      assert.equal(arity.length, 1, `expected exactly one arity finding for ${runtime}; got ${JSON.stringify(findings)}`);
      assert.match(
        arity[0],
        /rest parameter or an `arguments`-style body also reports 0 here/,
        'when the gate reports a zero-arity mismatch it must name the legitimate reason it might be wrong, or the operator has no way to tell a real drift from this case',
      );
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  }
});

/** Audit one throwaway module pair and return only its arity findings. */
async function arityFindings(mjs, dmts) {
  const dir = mkdtempSync(join(tmpdir(), 'eh-arity-'));
  try {
    writeFileSync(join(dir, 'm.mjs'), `${mjs}\n`);
    writeFileSync(join(dir, 'm.d.mts'), `${dmts}\n`);
    const { findings, census } = await auditLibDirectory(dir);
    return { arity: findings.filter((f) => f.includes('at runtime, but')), census };
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test('arity is read from a const of FUNCTION TYPE too, not only from a `function` declaration', async () => {
  // Gating on the declaration KEYWORD rather than on whether an arity was actually read skipped every
  // export declared `const handler: (a: A) => void` — an idiomatic spelling — so a parameter added to
  // or removed from its runtime went completely uncaught. Both spellings describe the same thing.
  const declaration = 'export declare const handler: (a: number, b: string) => void;';
  const drifted = await arityFindings('export const handler = (a, b, c, d) => [a, b, c, d];', declaration);
  assert.equal(drifted.arity.length, 1, `a const-declared function that gained parameters must be caught; got ${JSON.stringify(drifted.arity)}`);
  assert.equal(drifted.census.arityChecks, 1, 'the comparison must actually have run, not been skipped into a pass');

  const correct = await arityFindings('export const handler = (a, b) => [a, b];', declaration);
  assert.deepEqual(correct.arity, [], 'a matching const-declared function must stay green');
  assert.equal(correct.census.arityChecks, 1);

  // A type this cannot read without RESOLVING it is skipped rather than guessed at — that is a
  // compiler's job. The census makes the skip visible instead of letting it look like a comparison.
  for (const opaque of ['export interface Cb { (a: number): void }\nexport declare const handler: Cb;', 'export declare const handler: (1 | 2);']) {
    const skipped = await arityFindings('export const handler = (a, b, c) => [a, b, c];', opaque);
    assert.deepEqual(skipped.arity, [], `an unresolvable type must be skipped, never guessed: ${opaque}`);
    assert.equal(skipped.census.arityChecks, 0, 'a skipped arity must be reported as zero comparisons, not as a passing one');
  }
});

test('each overload is satisfied SEPARATELY — their union is a range no signature declares', async () => {
  // Taking the min of the mins and the max of the maxes builds a range nothing actually declares:
  // against `f(a)` and `f(a, b, c, d, e)` the union is 1-5, so a runtime `length` of 3 — matching
  // neither overload — was accepted. The union answers "is a call legal", which is not the question
  // this check asks: whether the runtime's single `length` corresponds to any declared shape at all.
  const overloads = 'export declare function f(a: number): void;\nexport declare function f(a: number, b: number, c: number, d: number, e: number): void;';
  const between = await arityFindings('export function f(a, b, c) { return [a, b, c]; }', overloads);
  assert.equal(between.arity.length, 1, `an arity matching NO overload must be caught; got ${JSON.stringify(between.arity)}`);
  assert.match(between.arity[0], /declares 1 or 5/, 'the message must name the declared shapes, not the union it is not');

  // Both real shapes must still be admitted, or the fix has simply become stricter than the truth.
  for (const [runtime, length] of [['export function f(a) { return a; }', 1], ['export function f(a, b, c, d, e) { return [a, b, c, d, e]; }', 5]]) {
    const ok = await arityFindings(runtime, overloads);
    assert.deepEqual(ok.arity, [], `a runtime length of ${length} matches a declared overload and must stay green`);
  }
});

test('a generic type-parameter list is skipped before the real parameters are read', () => {
  const { values } = extractDeclarationExports('export declare function pick<T extends (a: A) => B, U>(t: T, u: U): void;');
  assert.deepEqual(values.get('pick')[0].arity, { min: 2, max: 2 });
});

test('overload signatures WIDEN the admitted arity rather than each constraining it', () => {
  // A call matching any one overload is legal, so the runtime only has to satisfy their union.
  // Intersecting them instead would red-flag a perfectly correct runtime with two shapes.
  const { values } = extractDeclarationExports([
    'export declare function overloaded(a: A): void;',
    'export declare function overloaded(a: A, b: B, c: C): void;',
  ].join('\n'));
  assert.equal(values.get('overloaded').length, 2);
});

// ---------------------------------------------------------------------------------------------
// The runtime side is the module system itself, which is why the source side needs no parser.
// ---------------------------------------------------------------------------------------------

test('the runtime surface comes from the real import namespace, so every export spelling is seen', async () => {
  await withFixture({
    'shapes.mjs': [
      'export function plain(a, b) { return [a, b]; }',
      'export async function eager(a) { return a; }',
      'const local = 1;',
      'export { local as aliased };',
      'export const A = 1, ghost = 2;',
      'export default function fallback() {}',
    ].join('\n'),
  }, async (dir) => {
    const runtime = await loadRuntimeExports(join(dir, 'shapes.mjs'));
    assert.deepEqual(
      [...runtime.keys()].sort(),
      ['A', 'aliased', 'default', 'eager', 'ghost', 'plain'],
      'the loader answers this exactly; no source-side spelling can hide from it',
    );
    assert.equal(runtime.get('plain').length, 2);
    assert.equal(runtime.get('eager').length, 1, 'the lone async export — the spelling a fixed anchor silently skipped');
  });
});

test('#339 reproduced end to end: deleting the ASYNC export`s declaration no longer stays green', async () => {
  // The issue records this precise outcome as a measured false-green: "the lone async export was
  // silently skipped by an anchor that only matched plain `export function`, so deleting its
  // declaration stayed green".
  const declarationWith = 'export declare function plain(a: A, b: B): void;\nexport declare function eager(a: A): Promise<A>;\n';
  const declarationWithout = 'export declare function plain(a: A, b: B): void;\n';
  const runtime = 'export function plain(a, b) { return [a, b]; }\nexport async function eager(a) { return a; }\n';

  await withFixture({ 'm.mjs': runtime, 'm.d.mts': declarationWith }, async (dir) => {
    const { findings } = await auditLibDirectory(dir);
    assert.deepEqual(findings, [], `the control must be clean:\n  ${findings.join('\n  ')}`);
  });
  await withFixture({ 'm.mjs': runtime, 'm.d.mts': declarationWithout }, async (dir) => {
    const { findings } = await auditLibDirectory(dir);
    assert.ok(
      findings.some((f) => f.includes('runtime exports `eager` with no value declaration')),
      `deleting the async export's declaration must go RED; got:\n  ${findings.join('\n  ') || '(nothing)'}`,
    );
  });
});

// ---------------------------------------------------------------------------------------------
// The extractor against the real declarations, so the fixtures above cannot drift away from the tree.
// ---------------------------------------------------------------------------------------------

test('the extractor reads the shipped declarations with no unsupported construct anywhere', async () => {
  const files = (await readdir(LIB_DIR)).filter((f) => f.endsWith('.d.mts')).sort();
  assert.ok(files.length > 0, 'the declaration enumeration matched NOTHING — an empty sweep is not a clean one');
  const problems = [];
  let declared = 0;
  for (const file of files) {
    const { values, types, unsupported } = extractDeclarationExports(await readFile(join(LIB_DIR, file), 'utf8'), file);
    problems.push(...unsupported);
    declared += values.size + types.size;
  }
  assert.deepEqual(problems, [], `a shipped declaration uses a construct this extractor refuses to read:\n  ${problems.join('\n  ')}`);
  assert.ok(
    declared >= 165,
    `the extractor read only ${declared} declarations across ${files.length} files; 165 (89 value + 76 type) were present when this gate was written`,
  );
});
