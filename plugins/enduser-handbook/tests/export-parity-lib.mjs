// enduser-handbook — statement-aware extraction of a `.d.mts` export surface, and the audit that
// compares it against the REAL runtime namespace of the sibling `.mjs` (#339, #420).
//
// Why this is not a regex. Issue #339 records five consecutive rounds in which a regex/allowlist
// design produced a measured false-green, each escalation growing a new hole rather than closing the
// class: a prefix census that a second spelling defeats outright (`export function` vs
// `export declare function`, `export function` vs `export async function`), `^export\b` blind to an
// indented export, a declarator list `export const A = 1, ghost = 2` yielding only its first binding,
// the TypeScript-only `export declare const A: 1, ghost: 2`, a declarator list split across several
// lines, and two export statements on one physical line. Every one of those is a property of where
// the text sits, not of what it spells, so the fix is to know where you are — comment, string,
// template, or live code, and at what bracket depth — before reading anything. That is what
// `maskInert` plus depth tracking buys, and it is why this file exists instead of a sixth regex.
//
// Two directions, two declaration spaces. The runtime side is Node's own import namespace, never a
// parse of the `.mjs`: the module system is the authority on what a module exports, and asking it
// removes the entire source-side false-green inventory at a stroke. The declaration side has no
// runtime, so it is parsed — but only the shapes a `.d.mts` can actually hold, with every other shape
// a hard failure rather than a silent skip. TypeScript keeps VALUES and TYPES in separate declaration
// spaces: `export interface Foo` and `export type Foo` never exist at runtime, so only value-space
// declarations are compared against the namespace. Folding the two together would demand a runtime
// export for every interface and flood the gate with false reds.
//
// What this does NOT check: whether a declared TYPE is correct. `export declare function f(x: number)`
// against a runtime `f` that wants a string is invisible here, and closing that needs a real
// TypeScript compiler over a repo that ships no manifest, no tsconfig and no dependency of any kind
// (#420 option 1). This gate is #420 option 2, widened to the general case #339 asks for: names both
// ways, arity where a signature is declared, and no orphan module on either side.

import { readdirSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

/** Characters that continue an identifier — used for the `export` word-boundary test. */
const IDENT = /[A-Za-z0-9_$]/;

/** Declaration heads that introduce a TYPE-space name only (erased before any runtime exists). */
const TYPE_ONLY_HEADS = new Set(['interface', 'type']);

/** Modifiers that may sit between `export` and the declaration head, in any order TypeScript allows. */
const MODIFIERS = new Set(['declare', 'abstract', 'async', 'default']);

/**
 * Blank every comment, string and template literal, preserving byte offsets and line breaks.
 *
 * Offsets are preserved rather than the text removed so that a name found in the masked copy can be
 * sliced straight out of the original at the same index — an extractor that rewrites its input has to
 * keep a position map, and a wrong map is its own silent-drift class.
 *
 * Unterminated constructs are a hard failure, never a blank-to-end-of-file: swallowing the tail of a
 * file is exactly the shape that reads green while checking nothing.
 */
export function maskInert(source) {
  const out = Array.from(source);
  const n = source.length;
  const blank = (from, to) => {
    for (let k = from; k < to && k < n; k += 1) if (out[k] !== '\n') out[k] = ' ';
  };

  /** Skip a quoted string starting at `start` (which is the quote). Returns the index after it. */
  const skipQuoted = (start) => {
    const quote = source[start];
    let j = start + 1;
    while (j < n) {
      if (source[j] === '\\') { j += 2; continue; }
      if (source[j] === '\n') break; // an ordinary string cannot span a raw newline
      if (source[j] === quote) return j + 1;
      j += 1;
    }
    throw new SyntaxError(`unterminated ${quote === '"' ? 'double' : 'single'}-quoted string at offset ${start}`);
  };

  /**
   * Skip a template literal, including any `${ ... }` substitution — which is live code and may hold
   * nested strings and nested templates, so this recurses rather than counting braces alone.
   */
  const skipTemplate = (start) => {
    let j = start + 1;
    while (j < n) {
      if (source[j] === '\\') { j += 2; continue; }
      if (source[j] === '`') return j + 1;
      if (source[j] === '$' && source[j + 1] === '{') {
        let depth = 1;
        j += 2;
        while (j < n && depth > 0) {
          const c = source[j];
          if (c === '{') { depth += 1; j += 1; continue; }
          if (c === '}') { depth -= 1; j += 1; continue; }
          if (c === '"' || c === "'") { j = skipQuoted(j); continue; }
          if (c === '`') { j = skipTemplate(j); continue; }
          if (c === '/' && source[j + 1] === '/') { while (j < n && source[j] !== '\n') j += 1; continue; }
          if (c === '/' && source[j + 1] === '*') { j = skipBlockComment(j); continue; }
          j += 1;
        }
        if (depth > 0) throw new SyntaxError(`unterminated template substitution at offset ${start}`);
        continue;
      }
      j += 1;
    }
    throw new SyntaxError(`unterminated template literal at offset ${start}`);
  };

  /** Skip a `/* ... *\/` comment starting at `start`. Returns the index after it. */
  function skipBlockComment(start) {
    let j = start + 2;
    while (j < n) {
      if (source[j] === '*' && source[j + 1] === '/') return j + 2;
      j += 1;
    }
    throw new SyntaxError(`unterminated block comment at offset ${start}`);
  }

  let i = 0;
  while (i < n) {
    const ch = source[i];
    if (ch === '/' && source[i + 1] === '/') {
      let j = i;
      while (j < n && source[j] !== '\n') j += 1;
      blank(i, j);
      i = j;
      continue;
    }
    if (ch === '/' && source[i + 1] === '*') {
      const end = skipBlockComment(i);
      blank(i, end);
      i = end;
      continue;
    }
    if (ch === '"' || ch === "'") {
      const end = skipQuoted(i);
      // Leave the quotes themselves in place: they carry no bracket meaning, and keeping them makes a
      // masked slice still readable when a failure message quotes it.
      blank(i + 1, end - 1);
      i = end;
      continue;
    }
    if (ch === '`') {
      const end = skipTemplate(i);
      blank(i + 1, end - 1);
      i = end;
      continue;
    }
    i += 1;
  }
  return out.join('');
}

/**
 * A cursor over masked text. Names are read from the masked copy on purpose: an identifier is never
 * masked, so the two agree byte for byte wherever a name can legally appear, and reading from the
 * masked copy means a name can never be sourced from inside a comment or a string.
 */
class Reader {
  constructor(text, pos = 0) {
    this.text = text;
    this.pos = pos;
  }

  skipSpace() {
    while (this.pos < this.text.length && /\s/.test(this.text[this.pos])) this.pos += 1;
    return this;
  }

  /** The next identifier-shaped word without consuming it, or '' if the next token is not a word. */
  peekWord() {
    const save = this.pos;
    this.skipSpace();
    let end = this.pos;
    while (end < this.text.length && IDENT.test(this.text[end])) end += 1;
    const word = this.text.slice(this.pos, end);
    this.pos = save;
    return word;
  }

  /** Consume and return the next identifier-shaped word, or '' if the next token is not a word. */
  takeWord() {
    this.skipSpace();
    const start = this.pos;
    while (this.pos < this.text.length && IDENT.test(this.text[this.pos])) this.pos += 1;
    return this.text.slice(start, this.pos);
  }

  /** The next non-space character without consuming it, or '' at end of text. */
  peekChar() {
    const save = this.pos;
    this.skipSpace();
    const ch = this.text[this.pos] ?? '';
    this.pos = save;
    return ch;
  }
}

/** 1-based line number of a byte offset, for failure messages that a human can act on. */
function lineOf(source, offset) {
  let line = 1;
  for (let i = 0; i < offset && i < source.length; i += 1) if (source[i] === '\n') line += 1;
  return line;
}

/**
 * Skip a balanced `<...>` type-parameter list starting at the `<`. Returns the index after the `>`.
 *
 * `=>` is the trap: its `>` would otherwise close the list one level early on a constraint like
 * `<T extends (x: A) => B>`, so an `=` immediately followed by `>` consumes both characters as one
 * token. Brackets and braces are tracked too, since a constraint may contain either.
 */
function skipAngles(text, start) {
  let depth = 0;
  let i = start;
  while (i < text.length) {
    const ch = text[i];
    if (ch === '=' && text[i + 1] === '>') { i += 2; continue; }
    if (ch === '<') { depth += 1; i += 1; continue; }
    if (ch === '>') {
      depth -= 1;
      i += 1;
      if (depth === 0) return i;
      continue;
    }
    i += 1;
  }
  throw new SyntaxError(`unterminated type-parameter list at offset ${start}`);
}

/** Skip a balanced bracket group starting at `start` (an opener). Returns the index after the closer. */
function skipBracketed(text, start) {
  const pairs = { '(': ')', '[': ']', '{': '}' };
  const stack = [pairs[text[start]]];
  let i = start + 1;
  while (i < text.length && stack.length > 0) {
    const ch = text[i];
    if (ch in pairs) stack.push(pairs[ch]);
    else if (ch === stack[stack.length - 1]) stack.pop();
    i += 1;
  }
  if (stack.length > 0) throw new SyntaxError(`unbalanced bracket group at offset ${start}`);
  return i;
}

/**
 * Split a parameter or declarator list at commas belonging to THIS list only.
 *
 * Angle brackets are tracked alongside the three bracket kinds because a type argument list carries
 * its own commas — `x: Map<string, number>` is one parameter, not two. Angle tracking is ambiguous in
 * general TypeScript (`a < b, c > d` is a pair of comparisons), but not here: a `.d.mts` parameter or
 * declarator list holds types and nothing else — TypeScript forbids an initializer in an ambient
 * declaration — so there is no comparison expression for a `<` to belong to.
 */
function splitTopLevel(text) {
  const parts = [];
  let depth = 0;
  let angle = 0;
  let current = '';
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (ch === '=' && text[i + 1] === '>') { current += '=>'; i += 1; continue; }
    if (ch === '(' || ch === '[' || ch === '{') depth += 1;
    else if (ch === ')' || ch === ']' || ch === '}') depth -= 1;
    else if (ch === '<') angle += 1;
    else if (ch === '>' && angle > 0) angle -= 1;
    if (ch === ',' && depth === 0 && angle === 0) {
      parts.push(current);
      current = '';
      continue;
    }
    current += ch;
  }
  parts.push(current);
  return parts.map((p) => p.trim()).filter((p) => p !== '');
}

/**
 * The runtime arity a declared parameter list admits, as a RANGE, because a single number cannot be
 * sound here. `Function.prototype.length` counts the parameters before the first one carrying a
 * default or a rest — it does not count TypeScript optionality, and TypeScript spells a runtime
 * default as `x?: T` because an ambient declaration may not carry the initializer itself. So a
 * declaration `f(a: A, b?: B)` is satisfied by a runtime `length` of 1 (`function f(a, b = 1)`) and
 * equally by 2 (`function f(a, b)`); asserting either exact number would fail one legitimate module.
 * The range is what both spellings agree on, and it still catches the drift #420 is about: a
 * parameter added to or removed from the runtime moves `length` outside it.
 *
 * A leading `this: T` parameter is TypeScript's callee-type annotation, not a runtime parameter, and
 * is dropped before counting.
 */
export function declaredArity(paramText) {
  const params = splitTopLevel(paramText).filter((p, i) => !(i === 0 && /^this\s*[?:]/.test(p)));
  let min = params.length;
  let max = params.length;
  for (let i = 0; i < params.length; i += 1) {
    const p = params[i];
    if (p.startsWith('...')) { min = Math.min(min, i); max = Infinity; break; }
    // The binding sits before the first top-level `:`; `?` on it, or a default, makes it optional.
    const binding = splitOnTypeColon(p);
    if (binding.endsWith('?') || hasTopLevelDefault(p)) { min = Math.min(min, i); break; }
  }
  return { min, max };
}

/**
 * Whether a parameter carries a default value at its own top level.
 *
 * `=>` is checked first and consumed whole: an arrow-typed parameter (`cb: (a: A) => B`) carries an
 * `=` at depth 0 that a naive scan reads as a default, which would silently shrink the declared
 * minimum arity and turn a correct declaration into a false red.
 */
function hasTopLevelDefault(param) {
  let depth = 0;
  let angle = 0;
  for (let i = 0; i < param.length; i += 1) {
    const ch = param[i];
    if (ch === '=' && param[i + 1] === '>') { i += 1; continue; }
    if (ch === '(' || ch === '[' || ch === '{') depth += 1;
    else if (ch === ')' || ch === ']' || ch === '}') depth -= 1;
    else if (ch === '<') angle += 1;
    else if (ch === '>' && angle > 0) angle -= 1;
    else if (ch === '=' && depth === 0 && angle === 0 && param[i - 1] !== '=' && param[i - 1] !== '!' && param[i + 1] !== '=') return true;
  }
  return false;
}

/** The binding half of a parameter — everything before the annotation's top-level `:`. */
function splitOnTypeColon(param) {
  let depth = 0;
  let angle = 0;
  for (let i = 0; i < param.length; i += 1) {
    const ch = param[i];
    if (ch === '(' || ch === '[' || ch === '{') depth += 1;
    else if (ch === ')' || ch === ']' || ch === '}') depth -= 1;
    else if (ch === '<') angle += 1;
    else if (ch === '>' && angle > 0) angle -= 1;
    else if (ch === ':' && depth === 0 && angle === 0) return param.slice(0, i).trim();
  }
  return param.trim();
}

/**
 * Every export a `.d.mts` declares, split by declaration space.
 *
 * Returns `{ values, types, unsupported }`. `values` maps an exported name to the list of
 * declarations carrying it (a list, not one record, because TypeScript permits overload signatures —
 * several `export declare function f(...)` for one runtime function). `unsupported` names any
 * construct this extractor refuses to guess at; the audit turns each one into a finding, so an
 * unrecognized shape fails the gate instead of silently leaving a name out of the comparison.
 */
export function extractDeclarationExports(source, label = '<source>') {
  const masked = maskInert(source);
  const values = new Map();
  const types = new Set();
  const unsupported = [];

  const addValue = (name, record) => {
    if (!values.has(name)) values.set(name, []);
    values.get(name).push(record);
  };

  // An ambient `declare module` / `declare namespace` / `declare global` block re-homes every export
  // inside it into another module's surface, and its members sit at a bracket depth this scan skips.
  // Refusing the whole file is the only honest answer: silently reporting the depth-0 exports would
  // describe a surface the file does not have.
  for (const m of masked.matchAll(/(^|[^A-Za-z0-9_$])declare\s+(module|namespace|global)\b/g)) {
    unsupported.push(`${label}:${lineOf(source, m.index)}: ambient \`declare ${m[2]}\` block — this extractor reads a flat module surface only`);
  }

  let depth = 0;
  for (let i = 0; i < masked.length; i += 1) {
    const ch = masked[i];
    if (ch === '(' || ch === '[' || ch === '{') { depth += 1; continue; }
    if (ch === ')' || ch === ']' || ch === '}') { depth -= 1; continue; }
    if (depth !== 0 || ch !== 'e') continue;
    if (masked.slice(i, i + 6) !== 'export') continue;
    if (i > 0 && IDENT.test(masked[i - 1])) continue;
    if (IDENT.test(masked[i + 6] ?? '')) continue;

    const line = lineOf(source, i);
    const where = `${label}:${line}`;
    const reader = new Reader(masked, i + 6);

    let isDefault = false;
    let word = reader.peekWord();
    while (MODIFIERS.has(word)) {
      reader.takeWord();
      if (word === 'default') isDefault = true;
      word = reader.peekWord();
    }

    // `export type { A }` is a type-only re-export list; `export type A = ...` is a type alias. The
    // two are told apart by what follows the keyword, never by the keyword alone.
    if (word === 'type') {
      const save = reader.pos;
      reader.takeWord();
      if (reader.peekChar() === '{') {
        for (const name of readSpecifierList(masked, reader, where, unsupported)) types.add(name.exported);
        continue;
      }
      reader.pos = save;
    }

    if (TYPE_ONLY_HEADS.has(word)) {
      reader.takeWord();
      const name = reader.takeWord();
      if (!name) { unsupported.push(`${where}: \`export ${word}\` with no readable name`); continue; }
      types.add(name);
      continue;
    }

    if (word === 'function') {
      reader.takeWord();
      reader.skipSpace();
      if (masked[reader.pos] === '*') reader.pos += 1; // generator
      const name = isDefault ? 'default' : reader.takeWord();
      if (isDefault) reader.takeWord(); // an optional name on a default export is not the export name
      if (!name) { unsupported.push(`${where}: \`export function\` with no readable name`); continue; }
      reader.skipSpace();
      if (masked[reader.pos] === '<') reader.pos = skipAngles(masked, reader.pos);
      reader.skipSpace();
      if (masked[reader.pos] !== '(') {
        unsupported.push(`${where}: \`export function ${name}\` has no readable parameter list`);
        continue;
      }
      const close = skipBracketed(masked, reader.pos);
      const params = masked.slice(reader.pos + 1, close - 1);
      addValue(name, { kind: 'function', line, arity: declaredArity(params) });
      continue;
    }

    if (word === 'class' || word === 'enum') {
      reader.takeWord();
      const name = isDefault ? 'default' : reader.takeWord();
      if (!name) { unsupported.push(`${where}: \`export ${word}\` with no readable name`); continue; }
      addValue(name, { kind: word, line, arity: null });
      if (word === 'enum') types.add(name);
      continue;
    }

    if (word === 'const' || word === 'let' || word === 'var') {
      reader.takeWord();
      // `export declare const enum E { ... }` reaches here spelled as a `const`, but it is an enum
      // declaration with a member block, not a declarator list — reading declarators out of `enum E`
      // would invent a binding named `enum`.
      if (reader.peekWord() === 'enum') {
        reader.takeWord();
        const enumName = reader.takeWord();
        if (!enumName) { unsupported.push(`${where}: \`export ${word} enum\` with no readable name`); continue; }
        addValue(enumName, { kind: 'enum', line, arity: null });
        types.add(enumName);
        continue;
      }
      // The declarator list runs to the statement terminator. A `.d.mts` declarator carries a type
      // annotation and no initializer, so the terminator is the first top-level `;` — or, for a file
      // whose last statement omits it, end of text.
      const start = reader.pos;
      let end = start;
      let d = 0;
      let a = 0;
      while (end < masked.length) {
        const c = masked[end];
        if (c === '=' && masked[end + 1] === '>') { end += 2; continue; }
        if (c === '(' || c === '[' || c === '{') d += 1;
        else if (c === ')' || c === ']' || c === '}') d -= 1;
        else if (c === '<') a += 1;
        else if (c === '>' && a > 0) a -= 1;
        else if (c === ';' && d === 0 && a === 0) break;
        end += 1;
      }
      const declarators = splitTopLevel(masked.slice(start, end));
      if (declarators.length === 0) { unsupported.push(`${where}: \`export ${word}\` with no declarator`); continue; }
      for (const declarator of declarators) {
        const name = splitOnTypeColon(declarator).replace(/\s*=.*$/s, '').trim();
        if (!/^[A-Za-z_$][A-Za-z0-9_$]*$/.test(name)) {
          // A destructuring binding pattern exports several names through one declarator; reading it
          // would need a real binding-pattern parser, so it is refused rather than approximated.
          unsupported.push(`${where}: \`export ${word}\` declarator '${declarator.trim()}' is not a plain binding name`);
          continue;
        }
        addValue(name, { kind: word, line, arity: null });
      }
      i = end;
      continue;
    }

    if (isDefault) {
      // `export default <expression>;` — the namespace key is `default` whatever the expression is,
      // and this test must precede the specifier-list one: `export default { a: 1 }` opens with a
      // brace that is an object literal, not an export list.
      addValue('default', { kind: 'default-expression', line, arity: null });
      continue;
    }

    const next = reader.peekChar();
    if (next === '{') {
      for (const spec of readSpecifierList(masked, reader, where, unsupported)) {
        if (spec.typeOnly) types.add(spec.exported);
        else addValue(spec.exported, { kind: 'specifier', line, arity: null });
      }
      continue;
    }

    if (next === '*') {
      reader.skipSpace();
      reader.pos += 1;
      if (reader.peekWord() === 'as') {
        reader.takeWord();
        const name = reader.takeWord();
        if (!name) { unsupported.push(`${where}: \`export * as\` with no readable name`); continue; }
        addValue(name, { kind: 'namespace-reexport', line, arity: null });
        continue;
      }

      // A bare star re-export's surface is whatever the OTHER module exports, which this extractor
      // cannot know without resolving it. Reporting the names it can see would understate the
      // surface — the exact false-green shape #339 exists to stop.
      unsupported.push(`${where}: bare \`export * from\` — the re-exported surface is not statically knowable here`);
      continue;
    }

    unsupported.push(`${where}: unrecognized export construct — this extractor refuses to guess rather than skip it`);
  }

  return { values, types, unsupported };
}

/** Read an `{ a, b as c, type T }` specifier list at the reader's position. */
function readSpecifierList(masked, reader, where, unsupported) {
  reader.skipSpace();
  const open = reader.pos;
  if (masked[open] !== '{') {
    unsupported.push(`${where}: expected an export specifier list`);
    return [];
  }
  const close = skipBracketed(masked, open);
  reader.pos = close;
  const out = [];
  for (const raw of splitTopLevel(masked.slice(open + 1, close - 1))) {
    const parts = raw.split(/\s+/).filter(Boolean);
    let typeOnly = false;
    let tokens = parts;
    // `export { type as T }` re-exports a VALUE that happens to be named `type`; only a following
    // token that is not `as` makes the leading `type` an inline type-only marker.
    if (tokens[0] === 'type' && tokens.length > 1 && tokens[1] !== 'as') { typeOnly = true; tokens = tokens.slice(1); }
    const exported = tokens.length >= 3 && tokens[tokens.length - 2] === 'as'
      ? tokens[tokens.length - 1]
      : tokens[0];
    if (!/^[A-Za-z_$][A-Za-z0-9_$]*$/.test(exported ?? '')) {
      // A string-literal export name (`export { a as "b" }`) is legal ESM and has no place in these
      // modules; refusing it keeps the comparison keyed on real identifiers.
      unsupported.push(`${where}: export specifier '${raw}' does not name a plain identifier`);
      continue;
    }
    out.push({ exported, typeOnly });
  }
  return out;
}

/**
 * Every name the real module system says a `.mjs` exports, with each value's runtime shape.
 *
 * This is the whole reason the source side needs no parser: the loader already answers the question
 * exactly, for every spelling of `export` that exists — including the lone `export async function`
 * whose omission by a fixed-spelling anchor is one of #339's measured false-greens.
 */
export async function loadRuntimeExports(mjsPath) {
  const namespace = await import(pathToFileURL(mjsPath).href);
  const out = new Map();
  for (const name of Object.keys(namespace)) {
    const value = namespace[name];
    out.set(name, {
      isFunction: typeof value === 'function',
      length: typeof value === 'function' ? value.length : null,
    });
  }
  return out;
}

/**
 * Pair up `<base>.mjs` with `<base>.d.mts` across a directory, reporting BOTH orphan directions.
 *
 * The reverse direction is the point: a walk over `*.mjs` looking for a matching `.d.mts` never
 * enumerates a stale declaration-only module, so a `zombie.d.mts` whose runtime was deleted stays
 * invisible to it — enumerated as a false-green in #339 and unguarded here until now.
 */
export function enumerateModulePairs(libDir) {
  const runtime = new Set();
  const declaration = new Set();
  for (const entry of readdirSync(libDir, { withFileTypes: true })) {
    if (!entry.isFile()) continue;
    if (entry.name.endsWith('.d.mts')) declaration.add(entry.name.slice(0, -'.d.mts'.length));
    else if (entry.name.endsWith('.mjs')) runtime.add(entry.name.slice(0, -'.mjs'.length));
  }
  const bases = [...new Set([...runtime, ...declaration])].sort();
  return bases.map((base) => ({
    base,
    hasRuntime: runtime.has(base),
    hasDeclaration: declaration.has(base),
  }));
}

/**
 * Compare one module pair and return every disagreement as a finding string.
 *
 * Every finding names the module, the symbol and what was compared, because the repair for a name
 * mismatch (add or delete a declaration) and for an arity mismatch (re-read the signature) are
 * different jobs, usually for different people.
 */
export async function auditModulePair(libDir, base) {
  const findings = [];
  const declarationText = await readUtf8(join(libDir, `${base}.d.mts`));
  let extracted;
  try {
    extracted = extractDeclarationExports(declarationText, `${base}.d.mts`);
  } catch (error) {
    return {
      findings: [`${base}.d.mts: could not be read as a declaration file (${error.message}) — an unreadable declaration is a failure, never a skip`],
      census: { runtimeExports: 0, valueDeclarations: 0, typeDeclarations: 0, arityChecks: 0 },
    };
  }
  findings.push(...extracted.unsupported.map((u) => `unsupported declaration construct — ${u}`));

  let runtime;
  try {
    runtime = await loadRuntimeExports(join(libDir, `${base}.mjs`));
  } catch (error) {
    return {
      findings: [...findings, `${base}.mjs: failed to load (${error.message}) — the runtime surface could not be read, so no parity claim is made`],
      census: { runtimeExports: 0, valueDeclarations: extracted.values.size, typeDeclarations: extracted.types.size, arityChecks: 0 },
    };
  }

  for (const name of runtime.keys()) {
    if (!extracted.values.has(name)) {
      const hint = extracted.types.has(name)
        ? ` — \`${name}\` is declared, but only in TYPE space (interface/type), which does not exist at runtime`
        : '';
      findings.push(`${base}: runtime exports \`${name}\` with no value declaration in ${base}.d.mts${hint}`);
    }
  }

  for (const [name, records] of extracted.values) {
    if (!runtime.has(name)) {
      findings.push(`${base}: ${base}.d.mts declares \`${name}\` (${records[0].kind}, line ${records[0].line}) but the runtime does not export it`);
    }
  }

  let arityChecks = 0;
  for (const [name, records] of extracted.values) {
    const live = runtime.get(name);
    const signatures = records.filter((r) => r.kind === 'function' && r.arity);
    if (!live || !live.isFunction || signatures.length === 0) continue;
    // Overload signatures widen the admitted range rather than each constraining it: a call matching
    // any one of them is legal, so the runtime only has to satisfy their union.
    const min = Math.min(...signatures.map((s) => s.arity.min));
    const max = Math.max(...signatures.map((s) => s.arity.max));
    arityChecks += 1;
    if (live.length < min || live.length > max) {
      const range = max === Infinity ? `${min}+` : (min === max ? `${min}` : `${min}-${max}`);
      findings.push(`${base}: \`${name}\` takes ${live.length} parameter(s) at runtime, but ${base}.d.mts declares ${range} (line ${records[0].line}) — one of the two moved without the other`);
    }
  }

  return {
    findings,
    census: {
      runtimeExports: runtime.size,
      valueDeclarations: extracted.values.size,
      typeDeclarations: extracted.types.size,
      arityChecks,
    },
  };
}

async function readUtf8(path) {
  const { readFile } = await import('node:fs/promises');
  return readFile(path, 'utf8');
}

/**
 * Audit a whole `assets/lib`-shaped directory.
 *
 * The census travels with the findings on purpose. An enumeration that matched nothing produces an
 * empty finding list, which is byte-for-byte what a clean tree produces — so the caller is given the
 * counts it needs to refuse an implausible run instead of reading zero findings as zero problems.
 */
export async function auditLibDirectory(libDir) {
  const pairs = enumerateModulePairs(libDir);
  const findings = [];
  const modules = [];
  const census = {
    modules: 0,
    runtimeExports: 0,
    valueDeclarations: 0,
    typeDeclarations: 0,
    arityChecks: 0,
  };

  for (const pair of pairs) {
    if (!pair.hasDeclaration) {
      findings.push(`${pair.base}: ${pair.base}.mjs ships with no ${pair.base}.d.mts beside it`);
      continue;
    }
    if (!pair.hasRuntime) {
      findings.push(`${pair.base}: ${pair.base}.d.mts is a stale declaration-only module — no ${pair.base}.mjs exists for it to describe`);
      continue;
    }
    const result = await auditModulePair(libDir, pair.base);
    findings.push(...result.findings);
    modules.push({ base: pair.base, ...result.census });
    census.modules += 1;
    census.runtimeExports += result.census.runtimeExports;
    census.valueDeclarations += result.census.valueDeclarations;
    census.typeDeclarations += result.census.typeDeclarations;
    census.arityChecks += result.census.arityChecks;
  }

  return { findings, census, pairs, modules };
}
