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
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

/** Characters that continue an identifier — used for the `export` word-boundary test. */
const IDENT = /[A-Za-z0-9_$]/;

/** Declaration heads that introduce a TYPE-space name only (erased before any runtime exists). */
const TYPE_ONLY_HEADS = new Set(['interface', 'type']);

/** Modifiers that may sit between `export` and the declaration head, in any order TypeScript allows. */
const MODIFIERS = new Set(['declare', 'abstract', 'async', 'default']);

/**
 * Why a function-valued export's declared arity was not compared, keyed by declaration kind.
 *
 * Every one of these is a signature that exists somewhere the reader does not go, NOT a signature
 * that is absent — so each is named in `arityUnread` rather than skipped. An earlier revision
 * excluded these kinds from the census outright, on the argument that a matched class pair should
 * not be flagged; that turned an unread arity into an invisible one, and a 4-parameter runtime
 * function re-exported as `export { f }` against a 1-parameter local declaration passed green with
 * `arityChecks: 0`. Naming the reason is the difference between "we did not check this" and
 * "we checked everything".
 *
 * The map is partial, and the omission is `const` / `let` / `var` only. Those land in `arityUnread`
 * by name alone because the reason belongs to the individual annotation — a type only a compiler
 * could resolve — rather than to the kind, so there is nothing kind-level to say about them. Every
 * other kind that can reach the branch has an entry here.
 *
 * Which kinds CAN reach it is not obvious and was got wrong twice, in both directions, so it is
 * recorded as measured fixtures rather than as reasoning. The gate one line above is
 * `live.isFunction`, on the RUNTIME binding — so what reaches this branch is decided by the runtime,
 * not by the declaration head, and a matched pair and a shape disagreement behave differently under
 * the same head:
 *
 *   - `enum` — a matched enum pair reports NOTHING (an enum is an object at runtime, so the gate
 *     rejects it). But `export declare enum Color {}` against a runtime `export function Color(a)`
 *     IS a function at runtime and does reach here. An earlier revision of this comment claimed enum
 *     could not reach it at all, generalising from the matched pair alone.
 *   - `namespace-reexport` — the same shape: a real namespace pair never reaches here, and the only
 *     way `export * as ns` lands is a runtime function under it.
 *   - `interface` / `type` — genuinely unreachable, and for a stronger reason than the gate: a
 *     type-space head never enters `values` at all, so there is no record for the loop to reach.
 *
 * That `enum` and `namespace-reexport` are reachable ONLY through a declaration/runtime shape
 * disagreement is itself worth knowing: such a pair produces no finding, because the NAMES agree in
 * both directions, and surfaces only as a census entry. Tracked in #577 with the linking work.
 */
const ARITY_NOT_READ_BECAUSE = {
  // "if it declares any" is load-bearing: `export declare class Widget {}` has no `constructor`
  // member at all, and the earlier wording asserted one was there. Both spellings are unread all the
  // same — the reader does not descend into a class body either way — but a reason that describes a
  // member the file does not contain sends its reader looking for the wrong thing.
  class: 'its parameters, if it declares any, sit on a `constructor` member, which this reader does not descend into',
  specifier: 'a specifier is a name only, and the signature it re-exports sits in a local declaration this reader does not link back to',
  'namespace-reexport': 'the declaration says this is a re-exported module namespace while the runtime binding is a function — a shape disagreement the name comparison cannot see',
  enum: 'the declaration says this is an enum while the runtime binding is a function — a shape disagreement the name comparison cannot see',
  'default-expression': 'a default export expression carries no declared signature of its own',
};


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
  // `split('')` and NOT `Array.from(source)`: the two disagree on astral characters. `Array.from`
  // splits by code POINT, while every read below (`source[i]`) and every offset this function hands
  // back index by UTF-16 code UNIT — so one emoji in a doc comment shortens the array and silently
  // shifts the mask off by one for the whole rest of the file. Nothing would throw; the offsets would
  // just stop meaning what the caller thinks they mean, which is the failure this gate exists to
  // catch, arriving inside the gate itself.
  const out = source.split('');
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
    const word = this.takeWord();
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
 * Walk `text`, reporting for each character whether it sits at this text's OWN top level — outside
 * every bracket group and every `<...>` type-argument list.
 *
 * Four scans below need exactly this and nothing else: split a list at its own commas, find a
 * binding's annotation colon, spot a default, find a statement's terminator. Each was written wrong
 * at least once on the same two traps, so both are answered here once rather than four times. An
 * arrow `=>` is yielded as the single token `'=>'`, because its `>` closes nothing and its `=` is not
 * a default. A type-argument list is tracked alongside the three bracket kinds, because it carries
 * its own commas — `x: Map<string, number>` is one parameter, not two.
 *
 * Angle tracking is ambiguous in general TypeScript (`a < b, c > d` is a pair of comparisons), but
 * not over the text scanned here: a `.d.mts` parameter or declarator list holds types and nothing
 * else — TypeScript forbids an initializer in an ambient declaration — so there is no comparison
 * expression for a `<` to belong to.
 */
function* scanTopLevel(text) {
  let depth = 0;
  let angle = 0;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (ch === '=' && text[i + 1] === '>') {
      yield { index: i, char: '=>', top: depth === 0 && angle === 0 };
      i += 1;
      continue;
    }
    if (ch === '(' || ch === '[' || ch === '{') depth += 1;
    else if (ch === ')' || ch === ']' || ch === '}') depth -= 1;
    else if (ch === '<') angle += 1;
    else if (ch === '>' && angle > 0) angle -= 1;
    yield { index: i, char: ch, top: depth === 0 && angle === 0 };
  }
}

/** Split a parameter or declarator list at commas belonging to THIS list only. */
function splitTopLevel(text) {
  const parts = [];
  let current = '';
  for (const { char, top } of scanTopLevel(text)) {
    if (char === ',' && top) {
      parts.push(current);
      current = '';
      continue;
    }
    current += char;
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
  for (let i = 0; i < params.length; i += 1) {
    const p = params[i];
    // A rest parameter is where `Function.prototype.length` stops counting and where the declaration
    // stops constraining, so everything from here on is admitted.
    if (p.startsWith('...')) return { min: i, max: Infinity };
    // The binding sits before the first top-level `:`; `?` on it, or a default, makes it optional.
    if (splitOnTypeColon(p).endsWith('?') || hasTopLevelDefault(p)) return { min: i, max: params.length };
  }
  return { min: params.length, max: params.length };
}

/**
 * Whether a parameter carries a default value at its own top level.
 *
 * `==`, `!=` and `===` are not defaults, and neither is the `=` of an arrow — which the scan hands
 * over whole. An arrow-typed parameter (`cb: (a: A) => B`) carries an `=` at depth 0 that a naive
 * scan reads as a default, which would silently shrink the declared minimum arity and turn a correct
 * declaration into a false red.
 */
function hasTopLevelDefault(param) {
  for (const { index, char, top } of scanTopLevel(param)) {
    if (char !== '=' || !top) continue;
    if (param[index - 1] === '=' || param[index - 1] === '!' || param[index + 1] === '=') continue;
    return true;
  }
  return false;
}

/**
 * The declared arity of a binding whose TYPE is a function type — `const handler: (a: A) => void`.
 *
 * Arity used to be read only from a `function` declaration, so every export declared as a const of
 * arrow type was skipped entirely and silently: a parameter added to or removed from its runtime went
 * uncaught. The two spellings describe the same thing and both are idiomatic, so both are read.
 *
 * Returns null for anything that is not directly an arrow type — a named callable interface, an
 * object type carrying a call signature, an overloaded type. Those would each need the type RESOLVED
 * rather than read, which is a compiler's job (#573); returning null skips the arity check for that
 * binding exactly as before, rather than guessing at it.
 */
function functionTypeArity(annotation) {
  let text = annotation.trim();
  // Redundant parentheses around the whole type are pure grouping — `((a: A) => void)` declares
  // exactly what `(a: A) => void` declares — but they hide the arrow behind a group that spans the
  // entire annotation, so the arrow test below never fires and the arity is skipped without a word.
  // Unwrapping is safe precisely because it is only done when the group spans EVERYTHING: a real
  // parameter list is always followed by its arrow, so it never spans the whole annotation, and a
  // parenthesized non-function type (`(A | B)`) unwraps to something the arrow test then rejects
  // anyway. Each pass strictly shortens the text, so this terminates.
  while (text.startsWith('(') && skipBracketed(text, 0) === text.length) {
    text = text.slice(1, -1).trim();
  }
  let i = 0;
  if (text[i] === '<') {
    i = skipAngles(text, i);
    while (/\s/.test(text[i] ?? '')) i += 1;
  }
  if (text[i] !== '(') return null;
  const close = skipBracketed(text, i);
  let j = close;
  while (/\s/.test(text[j] ?? '')) j += 1;
  // Without the arrow this is a parenthesized type, not a signature — `const x: (A | B)` declares no
  // parameters at all, and reading one out of it would invent an arity nothing stated.
  if (text[j] !== '=' || text[j + 1] !== '>') return null;
  return declaredArity(text.slice(i + 1, close - 1));
}

/** The annotation half of a declarator — everything after its top-level `:`, or '' if untyped. */
function annotationOf(declarator) {
  for (const { index, char, top } of scanTopLevel(declarator)) {
    if (char === ':' && top) return declarator.slice(index + 1);
  }
  return '';
}

/** The binding half of a parameter — everything before the annotation's top-level `:`. */
function splitOnTypeColon(param) {
  for (const { index, char, top } of scanTopLevel(param)) {
    if (char === ':' && top) return param.slice(0, index).trim();
  }
  return param.trim();
}

/**
 * The offset of the `;` ending the statement that begins at `start`, or end of text for a file whose
 * last statement omits it. A `.d.mts` declarator carries a type annotation and no initializer, so the
 * first top-level `;` is the terminator.
 */
function findStatementEnd(text, start) {
  for (const { index, char, top } of scanTopLevel(text.slice(start))) {
    if (char === ';' && top) return start + index;
  }
  return text.length;
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
    // `m.index` points at the boundary character the prefix group matched, not at `declare` — and
    // when that character is the preceding newline, the reported line is the one above the block.
    unsupported.push(`${label}:${lineOf(source, m.index + m[1].length)}: ambient \`declare ${m[2]}\` block — this extractor reads a flat module surface only`);
  }

  // A STACK of expected closers, not a counter. Both agree on depth, so a counter never changes which
  // exports get extracted — but only a stack can tell that `[number)` is not a bracket pair at all.
  // These declarations are hand-maintained with no compiler anywhere in the repository, so a file
  // that is not valid TypeScript reaches this extractor unchallenged, and claiming to have read it
  // reliably is a claim this had no basis for making.
  const CLOSER_FOR = { '(': ')', '[': ']', '{': '}' };
  const stack = [];
  // The offset of the FIRST bad closer, not merely the fact of one: an end-of-file report tells a
  // reader that some bracket somewhere is wrong, which in a 700-line hand-maintained declaration is
  // the least actionable form the same information can take.
  let firstBadCloser = -1;
  let mismatchedKind = false;
  for (let i = 0; i < masked.length; i += 1) {
    const ch = masked[i];
    if (ch === '(' || ch === '[' || ch === '{') { stack.push(CLOSER_FOR[ch]); continue; }
    if (ch === ')' || ch === ']' || ch === '}') {
      if (stack.length === 0) {
        if (firstBadCloser === -1) firstBadCloser = i;
      } else if (stack[stack.length - 1] !== ch) {
        if (firstBadCloser === -1) { firstBadCloser = i; mismatchedKind = true; }
        stack.pop();
      } else {
        stack.pop();
      }
      continue;
    }
    const depth = stack.length;
    if (ch !== 'e') continue;
    if (masked.slice(i, i + 6) !== 'export') continue;
    if (i > 0 && IDENT.test(masked[i - 1])) continue;
    if (IDENT.test(masked[i + 6] ?? '')) continue;

    if (depth !== 0) {
      // THE invariant, and the only one here that is not a proxy: no export declaration may sit
      // where this top-level scan will not read it. A stray opener and a stray closer STRADDLING a
      // declaration leave the brackets balanced and every kind matched, so neither check below sees
      // anything wrong, while the declaration between them is skipped and reported by nothing — and a
      // name that never enters the extracted set is a name no comparison in either direction can
      // miss. Bracket balance and bracket kinds are corroborating syntax checks; this is the guard.
      //
      // The polarity matters more than the check. An earlier revision listed the declaration HEADS
      // that count as a hidden export, and every spelling absent from that list — `export { ghost }`,
      // `export * as ghost`, `export default ghost`, `export namespace ghost` — passed silently,
      // because an allowlist of what is FORBIDDEN fails green on everything it forgot. Inverted: the
      // small closed set is what may legally follow a member NAMED `export` inside a body, and
      // anything else is refused. A spelling nobody thought of now fails RED, which is the direction
      // a gate is allowed to be wrong in.
      //
      // The set is closed because the grammar closes it: an identifier in a type or interface body is
      // followed by `:` or `?` (property), `(` or `<` (method), and in an enum body by `,`, `;` or
      // `}`. There is no member position in which a declaration keyword may follow it.
      //
      // `=` is deliberately NOT in the set, though an enum member may carry an initializer. `export =`
      // is itself a TypeScript export form — one this extractor does not support and refuses at the
      // top level — so admitting `=` here would let a nested `export = ghost` pass as a member and
      // take its refusal with it, which is the fail-closed hole this whole check exists to shut. The
      // cost is that an enum member literally named `export` and carrying an initializer fails red;
      // it fails LOUD, no shipped module has an enum at all, and that is the direction to be wrong in.
      const MEMBER_CONTINUATIONS = new Set([':', '?', '(', '<', ',', ';', '}']);
      const probe = new Reader(masked, i + 6);
      const next = probe.peekChar();
      if (!MEMBER_CONTINUATIONS.has(next)) {
        const spelling = source.slice(i, i + 48).split('\n')[0].trim();
        unsupported.push(`${label}:${lineOf(source, i)}: \`${spelling}\` sits inside a nested block (bracket depth ${depth}), where it is neither a member named \`export\` nor anything this scan will read — the file's export surface is reported as unknown rather than as complete`);
      }
      continue;
    }

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
      const start = reader.pos;
      const end = findStatementEnd(masked, start);
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
        addValue(name, { kind: word, line, arity: functionTypeArity(annotationOf(declarator)) });
      }
      // Deliberately NOT jumping the cursor past the declarator. Doing so skipped the outer scanner
      // over the whole statement, and the outer scanner is what matches bracket KINDS — so
      // `export declare const A: [number);` was extracted as a clean declaration while being invalid
      // TypeScript, which is the very thing the kind stack was added to catch. Letting the loop walk
      // the declarator costs nothing (it holds no `export` keyword of its own to re-read) and keeps
      // one scanner responsible for brackets everywhere instead of two that can disagree.
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

  // The second of the two corroborating syntax checks (the first is the kind match, made as each
  // closer is read). Everything above trusts bracket depth to say which `export` is a module export,
  // so depth is the one thing that must not be trusted silently: if masking ever mis-reads a
  // construct and swallows a closer, depth never returns to zero and every later top-level export is
  // skipped, with no finding raised and an empty surface reported as a clean one.
  //
  // Neither of these two is the real guard, and it is worth being explicit about that, because three
  // separate review findings landed here and patching each one in turn would have been the wrong
  // answer. Balance and kind-matching detect a CORRUPTED FILE; they are proxies, and a proxy can
  // always be satisfied by a corruption shaped to satisfy it. The guard is the nested-export check
  // above, which tests the thing that actually matters — is a real export declaration sitting
  // somewhere this scan will not read it — and is written so that an unrecognized spelling fails red.
  // These two stay because a broken file is worth naming on its own, and because they name a LINE.
  if (stack.length !== 0 || firstBadCloser !== -1) {
    const where = firstBadCloser !== -1
      ? `, first ${mismatchedKind ? 'mismatched' : 'unmatched'} closer at ${label}:${lineOf(source, firstBadCloser)}`
      : '';
    unsupported.push(`${label}: brackets did not pair up (${stack.length} still open at end of file${where}) — the file could not be read reliably, so its export surface is reported as unknown rather than as empty`);
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
  const declarationText = await readFile(join(libDir, `${base}.d.mts`), 'utf8');
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
  const arityUnread = [];
  for (const [name, records] of extracted.values) {
    const live = runtime.get(name);
    // Any declaration that resolved a parameter list counts, not only a `function` one — a binding
    // declared `const handler: (a: A, b: B) => void` is a function too, and gating on the KEYWORD
    // rather than on whether an arity was actually read skipped every one of them.
    const signatures = records.filter((r) => r.arity);
    if (!live || !live.isFunction) continue;
    if (signatures.length === 0) {
      // A class reaches this branch too — `typeof` reports "function" for one — and every kind that
      // lands here has a declared signature SOMEWHERE this reader does not go, never an absent one.
      // `ARITY_NOT_READ_BECAUSE` at the top of the file holds the per-kind reason the entry carries.
      //
      // The class-closing half. Three separate review findings were all the same shape: a function
      // export whose declared parameter list this could not read, skipped in SILENCE — first every
      // const-declared one, then one behind redundant parentheses. Each was fixed by teaching the
      // reader one more spelling, which only ever shortens the list of spellings nobody has thought
      // of yet. What actually closes the class is refusing to skip quietly: the runtime says this
      // export is a function, so an arity that was never compared is recorded by NAME. A reader
      // whose type genuinely cannot be resolved without a compiler still lands here, and that is the
      // correct outcome — it is a gap either way, and the only question is whether anyone can see it.
      const because = ARITY_NOT_READ_BECAUSE[records[0].kind];
      arityUnread.push(`${name} (${records[0].kind}, line ${records[0].line})${because ? ` — ${because}` : ''}`);
      continue;
    }
    arityChecks += 1;
    // The ENVELOPE across all signatures, not a per-signature match. An earlier revision required the
    // runtime `length` to fall inside some ONE overload's range, on the argument that the union
    // admits an arity no signature declares. That argument was wrong, and it is worth writing down
    // why, because it sounded right for a full round: `Function.prototype.length` is the
    // implementation's formal-parameter PREFIX, not the set of call arities it accepts. Overloads
    // `f(a)` and `f(a, b, c, d, e)` are legitimately implemented here as `function f(a, b, c)` reading
    // `arguments[3]` and `arguments[4]` for the long branch — these are hand-written declarations over
    // plain JavaScript, with no TypeScript implementation signature to constrain them. Requiring an
    // exact overload match rejects that valid implementation, so the envelope stands.
    const min = Math.min(...signatures.map((s) => s.arity.min));
    const max = Math.max(...signatures.map((s) => s.arity.max));
    if (live.length < min || live.length > max) {
      const range = max === Infinity ? `${min}+` : (min === max ? `${min}` : `${min}-${max}`);
      // The same `length`-is-a-prefix fact is the one way this check can be wrong, and it is not
      // confined to zero: any implementation that reads `arguments` past its named parameters, or
      // gathers them with a rest parameter, reports fewer than it accepts. So the message states the
      // fact and asks which side moved, rather than telling anyone to relax the declaration —
      // weakening a correct declaration to satisfy a heuristic would trade a loud wrong answer for a
      // quiet one, which is the trade this gate exists to refuse.
      findings.push(`${base}: \`${name}\` takes ${live.length} parameter(s) at runtime, but ${base}.d.mts declares ${range} (line ${records[0].line}) — \`Function.prototype.length\` counts only the formal parameters before the first default or rest, so an implementation reading \`arguments\` or gathering a rest reports fewer than it accepts; check which of the two actually moved before changing either`);
    }
  }

  return {
    findings,
    arityUnread: arityUnread.map((entry) => `${base}: ${entry}`),
    census: {
      runtimeExports: runtime.size,
      valueDeclarations: extracted.values.size,
      typeDeclarations: extracted.types.size,
      arityChecks,
      arityUnread: arityUnread.length,
    },
  };
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
  // Exports the runtime says are functions whose declared parameter list could not be read. Not
  // findings — a declaration may legitimately name a type only a compiler could resolve — but never
  // silent either, which is the whole point.
  const arityUnread = [];
  const census = {
    modules: 0,
    runtimeExports: 0,
    valueDeclarations: 0,
    typeDeclarations: 0,
    arityChecks: 0,
    arityUnread: 0,
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
    arityUnread.push(...(result.arityUnread ?? []));
    modules.push({ base: pair.base, ...result.census });
    census.modules += 1;
    census.runtimeExports += result.census.runtimeExports;
    census.valueDeclarations += result.census.valueDeclarations;
    census.typeDeclarations += result.census.typeDeclarations;
    census.arityChecks += result.census.arityChecks;
    census.arityUnread += result.census.arityUnread ?? 0;
  }

  return { findings, census, pairs, modules, arityUnread };
}
