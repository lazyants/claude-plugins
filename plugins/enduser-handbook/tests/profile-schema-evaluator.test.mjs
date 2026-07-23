// Verification for #296 part 4: the example-profile-validates-against-schema gate.
//
// Two independent groups of tests:
//   - Schema-structure tests (fail-closed sweep, evaluator self-check) need no YAML parsing at all
//     and always run.
//   - Instance-validation tests need the REAL shipped handbook.profile.example.yml loaded through
//     Ruby/Psych (the ground-truth YAML loader, same rationale as profile-version.differential.test.mjs)
//     and are skipped — never silently passed — when `ruby -ryaml -rjson` is unavailable.
//
// Every RED probe below mutates a deep clone of the REAL loaded example (never the schema, except
// the dedicated fail-closed sweep) and asserts the evaluator's `validate()` reports at least one
// error. Every probe is its own test so a reviewer can see exactly which evaluator behavior it
// isolates — see the inline comment on each for why it can't be swapped for a neighboring probe.

import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  validate,
  assertNoUnknownKeywords,
  enumerateSchemaNodes,
  getNodeAtSteps,
} from './profile-schema-evaluator.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const EVALUATOR_PATH = join(HERE, 'profile-schema-evaluator.mjs');
const SCHEMA_PATH = join(HERE, '..', 'skills/enduser-handbook/assets/profile.schema.json');
const EXAMPLE_PATH = join(HERE, '..', 'skills/enduser-handbook/assets/handbook.profile.example.yml');

const RUBY_AVAILABLE = spawnSync('ruby', ['-ryaml', '-rjson', '-e', '1'], { stdio: 'ignore' }).status === 0;

const schema = JSON.parse(readFileSync(SCHEMA_PATH, 'utf8'));

// clone(base) is used by every probe below so mutating one probe's copy can never leak into another.
function clone(obj) {
  return JSON.parse(JSON.stringify(obj));
}

// Loads the REAL shipped example through Ruby/Psych (ground truth, same YAML engine the plugin's own
// differential test uses) and re-emits it as JSON, exactly the pipeline reference-assets.test.sh's
// gate drives. Memoized so every ruby-gated test below shares one ruby spawn.
let baseInstanceCache;
function baseInstance() {
  if (baseInstanceCache === undefined) {
    const script = `
require 'yaml'
require 'json'
begin
  doc = Psych.safe_load(File.read(ARGV[0]), aliases: true)
  puts JSON.generate(doc)
rescue Exception => e
  STDERR.puts "ruby-load-error: #{e.class}: #{e.message}"
  exit 1
end
`;
    const result = spawnSync('ruby', ['-e', script, EXAMPLE_PATH], { encoding: 'utf8' });
    assert.equal(result.status, 0, `ruby failed to load ${EXAMPLE_PATH} via Psych: ${result.stderr}`);
    baseInstanceCache = JSON.parse(result.stdout);
  }
  return clone(baseInstanceCache);
}

// ==== schema-structure tests (no ruby needed) =======================================================

test('profile-schema-evaluator: real schema carries zero unrecognized keywords (baseline)', () => {
  assert.doesNotThrow(() => assertNoUnknownKeywords(schema));
});

test('profile-schema-evaluator: fail-closed check is a generic per-key membership loop, not a hardcoded 2-3-name check', () => {
  const src = readFileSync(EVALUATOR_PATH, 'utf8');
  assert.match(src, /ALLOWED_SCHEMA_KEYWORDS\.has\(key\)/, 'expected a generic Set#has(key) membership test');
  assert.match(src, /for \(const key of Object\.keys\(schema\)\)/, 'expected a loop over every key, not a fixed list of named keywords');
  // None of this run's rotation-mutation keyword literals (never used by the real allowlist) may
  // appear as a special-cased comparison in the evaluator's own source — if they did, the fail-closed
  // check would be hardcoded to specific names rather than deriving from ALLOWED_SCHEMA_KEYWORDS.
  for (const decoy of ['minLength', 'maxLength', 'oneOf']) {
    assert.ok(!src.includes(`'${decoy}'`) && !src.includes(`"${decoy}"`), `evaluator source unexpectedly special-cases '${decoy}'`);
  }
});

// ---- prototype-chain membership regression (lazy-ants-reviewer bot finding on PR #318) ------------
//
// `in` walks the prototype chain, so a required/declared key named after an inherited
// Object.prototype member ('toString', 'constructor', ...) would be satisfied by that inherited
// member even on a genuinely empty instance — two independent sites in `validate()`'s `walk()`:
// the required-key check, and the properties-descend check. The two probes below are deliberately
// NEVER combined in one schema: a schema with BOTH `required: ['toString']` AND a matching
// `properties.toString` entry still produced a nonzero error count under the pre-fix code, but for
// the WRONG reason — the properties-descend site's own leak (spuriously validating the inherited
// Object.prototype.toString FUNCTION against the declared schema) happened to also push an error,
// masking that the required-check site was separately and silently satisfied by prototype
// inheritance. Verified by hand against a scratch revert of the fix before wiring these in.

// No matching `properties` entry in the schemas below, deliberately — see the note above on why
// combining the two sites in one schema would mask this probe.
for (const key of ['toString', 'constructor']) {
  test(`RED (required-check prototype leak): a required key named after an inherited Object.prototype member ('${key}') is not satisfied by prototype-chain inheritance on an empty instance`, () => {
    const errors = validate({}, { type: 'object', required: [key] });
    assert.ok(errors.length > 0, `expected a missing-required-key error for "${key}", got none — prototype-chain leak`);
  });
}

test('GREEN (properties-descend prototype leak): an OPTIONAL declared property named after an inherited Object.prototype member (toString) is not spuriously validated against the inherited value on an object that never set it', () => {
  // Not `required`, deliberately, so only the properties-descend site's own membership check is
  // exercised. Pre-fix, this validated the inherited Object.prototype.toString FUNCTION against
  // the declared {"type":"number"} schema and produced a spurious type-mismatch on an object that
  // is otherwise perfectly schema-valid (it simply never set an optional "toString" property).
  const scratchSchema = { type: 'object', properties: { toString: { type: 'number' } } };
  const errors = validate({}, scratchSchema);
  assert.deepEqual(errors, [], `expected no errors on an object that never set 'toString', got: ${JSON.stringify(errors)}`);
});

test('profile-schema-evaluator: GENERATED fail-closed sweep — every schema node rejects an unrecognized keyword (single + double, rotating names)', () => {
  const nodes = enumerateSchemaNodes(schema);
  // Re-derived from the walk every run — never hardcoded — so this assertion tracks the schema as it grows.
  assert.ok(nodes.length > 10, `expected the schema walk to enumerate a nontrivial node count, got ${nodes.length}`);

  const rotation = ['minLength', 'maxLength', 'oneOf'];
  let single = 0;
  let double = 0;
  nodes.forEach(({ path, steps }, i) => {
    // (i) a single unrecognized keyword, cycling its name across nodes so no one fixed literal could
    // be gamed by a non-structural evaluator that merely greps the serialized schema for that string.
    const kw1 = rotation[i % rotation.length];
    const singleMutant = clone(schema);
    getNodeAtSteps(singleMutant, steps)[kw1] = true;
    assert.throws(
      () => assertNoUnknownKeywords(singleMutant),
      undefined,
      `node ${path}: inserting a single unrecognized keyword '${kw1}' was not caught`,
    );
    single += 1;

    // (ii) two distinct unrecognized keywords together, to catch an off-by-one like
    // `unknown.length === 1` instead of `> 0`.
    const kwA = rotation[i % rotation.length];
    const kwB = rotation[(i + 1) % rotation.length];
    const doubleMutant = clone(schema);
    const target = getNodeAtSteps(doubleMutant, steps);
    target[kwA] = true;
    target[kwB] = true;
    assert.throws(
      () => assertNoUnknownKeywords(doubleMutant),
      undefined,
      `node ${path}: inserting two unrecognized keywords '${kwA}'+'${kwB}' together was not caught`,
    );
    double += 1;
  });
  assert.equal(single, nodes.length);
  assert.equal(double, nodes.length);
});

// ==== instance-validation tests (need the real shipped example, ruby-gated) =========================

test('profile-schema-evaluator: the shipped handbook.profile.example.yml validates cleanly', { skip: !RUBY_AVAILABLE }, () => {
  const errors = validate(baseInstance(), schema);
  assert.deepEqual(errors, []);
  assert.doesNotThrow(() => assertNoUnknownKeywords(schema));
});

// ---- RED probes: each isolates exactly one evaluator behavior -------------------------------------

test('RED: a required key missing NESTED inside an object (language.code) — proves recursion into nested required/properties', { skip: !RUBY_AVAILABLE }, () => {
  const c = baseInstance();
  delete c.language.code;
  assert.ok(validate(c, schema).length > 0);
});

test('RED: an invalid enum value (stack.frontend.type)', { skip: !RUBY_AVAILABLE }, () => {
  const c = baseInstance();
  c.stack.frontend.type = 'not-a-real-frontend';
  assert.ok(validate(c, schema).length > 0);
});

test('RED: an invalid const value (profile_version) — separate from the enum probe above', { skip: !RUBY_AVAILABLE }, () => {
  const c = baseInstance();
  c.profile_version = 2;
  assert.ok(validate(c, schema).length > 0);
});

test('RED: a wrong type on a plain STRING field (language.register)', { skip: !RUBY_AVAILABLE }, () => {
  const c = baseInstance();
  c.language.register = 42;
  assert.ok(validate(c, schema).length > 0);
});

// role_flags has NO `required` of its own, so rejecting these two isolates the `type` check itself —
// an evaluator that never checks `type` at all would let both slip through purely because there is no
// missing-required-member for it to trip on (unlike `stack`, which is deliberately NOT used here).
test('RED: a wrong type on an OBJECT field with no required members (capture.role_flags: [])', { skip: !RUBY_AVAILABLE }, () => {
  const c = baseInstance();
  c.capture.role_flags = [];
  assert.ok(validate(c, schema).length > 0);
});

test('RED: a wrong type on an OBJECT field with no required members (capture.role_flags: null)', { skip: !RUBY_AVAILABLE }, () => {
  const c = baseInstance();
  c.capture.role_flags = null;
  assert.ok(validate(c, schema).length > 0);
});

test('RED: a wrong type on a BOOLEAN field (publish.frontmatter_required)', { skip: !RUBY_AVAILABLE }, () => {
  const c = baseInstance();
  c.publish.frontmatter_required = 'yes';
  assert.ok(validate(c, schema).length > 0);
});

test('RED: style_guide.source set outside its ["string","null"] union', { skip: !RUBY_AVAILABLE }, () => {
  const c = baseInstance();
  c.style_guide.source = 42;
  assert.ok(validate(c, schema).length > 0);
});

// A type-only `items` site (no minItems alongside it) given a non-conforming element — distinct from
// the diataxis probes below, which pair `items` with `minItems`.
test('RED: a type-only items site given a non-conforming element (capture.auth_role_enum: [42])', { skip: !RUBY_AVAILABLE }, () => {
  const c = baseInstance();
  c.capture.auth_role_enum = [42];
  assert.ok(validate(c, schema).length > 0);
});

test('RED: an empty diataxis.quadrants_in_use array — minItems violation, own probe', { skip: !RUBY_AVAILABLE }, () => {
  const c = baseInstance();
  c.diataxis.quadrants_in_use = [];
  assert.ok(validate(c, schema).length > 0);
});

test('RED: an invalid enum value inside a NON-EMPTY diataxis.quadrants_in_use array — items violation, separate from the empty-array probe', { skip: !RUBY_AVAILABLE }, () => {
  const c = baseInstance();
  c.diataxis.quadrants_in_use = ['tutorials', 'not-a-real-quadrant'];
  assert.ok(validate(c, schema).length > 0);
});

test('RED: a pattern violation (language.code)', { skip: !RUBY_AVAILABLE }, () => {
  const c = baseInstance();
  c.language.code = 'ENG';
  assert.ok(validate(c, schema).length > 0);
});

test('RED: a pattern violation (glossary.canonical_term_language)', { skip: !RUBY_AVAILABLE }, () => {
  const c = baseInstance();
  c.glossary.canonical_term_language = '1';
  assert.ok(validate(c, schema).length > 0);
});

test('RED: an additionalProperties:false violation (extra key inside style_guide.inline)', { skip: !RUBY_AVAILABLE }, () => {
  const c = baseInstance();
  c.style_guide.inline.extra_field = 'x';
  assert.ok(validate(c, schema).length > 0);
});

// additionalProperties-as-nested-schema, at TWO depths — both mandatory, neither an alternative to
// the other.
test('RED: additionalProperties-as-nested-schema (a) — capture.role_flags.admin given a non-array value proves the NESTED schema\'s own type is checked', { skip: !RUBY_AVAILABLE }, () => {
  const c = baseInstance();
  c.capture.role_flags.admin = 'oops';
  assert.ok(validate(c, schema).length > 0);
});

test('RED: additionalProperties-as-nested-schema (b) — capture.role_flags.admin: [42] proves the nested schema\'s own items:{"type":"string"} is evaluated', { skip: !RUBY_AVAILABLE }, () => {
  const c = baseInstance();
  c.capture.role_flags.admin = [42];
  assert.ok(validate(c, schema).length > 0);
});

// ---- positive controls ------------------------------------------------------------------------------

test('GREEN: a scratch copy correctly omitting the optional style_guide.inline object', { skip: !RUBY_AVAILABLE }, () => {
  const c = baseInstance();
  delete c.style_guide.inline;
  assert.deepEqual(validate(c, schema), []);
});

test('GREEN: style_guide.source explicitly set to null (the shipped example only exercises the string half of its union)', { skip: !RUBY_AVAILABLE }, () => {
  const c = baseInstance();
  c.style_guide.source = null;
  assert.deepEqual(validate(c, schema), []);
});

test('GREEN: an extra undeclared key inside an object with NO additionalProperties keyword at all (language.extra)', { skip: !RUBY_AVAILABLE }, () => {
  const c = baseInstance();
  c.language.extra = 'x';
  assert.deepEqual(validate(c, schema), []);
});

test('GREEN: an extra unknown top-level key (root additionalProperties: true, explicit)', { skip: !RUBY_AVAILABLE }, () => {
  const c = baseInstance();
  c.some_unknown_root_key = 'x';
  assert.deepEqual(validate(c, schema), []);
});

test('GREEN: an extra unknown key inside publish.section_labels (also explicit additionalProperties: true)', { skip: !RUBY_AVAILABLE }, () => {
  const c = baseInstance();
  c.publish.section_labels.extra = 'x';
  assert.deepEqual(validate(c, schema), []);
});

test('GREEN: a capture.role_flags entry that IS validly shaped (array of strings) is accepted', { skip: !RUBY_AVAILABLE }, () => {
  const c = baseInstance();
  c.capture.role_flags.external = ['SomeOtherRole'];
  assert.deepEqual(validate(c, schema), []);
});
