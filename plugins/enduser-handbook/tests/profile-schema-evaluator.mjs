// Test-only helper (#296): a small, recursive JSON-Schema evaluator scoped EXACTLY to the keyword
// set profile.schema.json actually uses. This is not a general-purpose JSON-Schema library — it
// implements only: type (string or union array), additionalProperties (true/absent/false/nested
// schema), required, properties, const, enum, pattern, items, minItems. Any other keyword
// encountered while walking the schema's own structure is treated as a hard error (see
// assertNoUnknownKeywords) so a schema author cannot silently add a keyword this evaluator does not
// enforce.
//
// Two independent traversals live here:
//   - validate(instance, schema): walks the INSTANCE against the schema, instance-shape-driven.
//   - collectUnknownKeywords(schema) / enumerateSchemaNodes(schema): walk the SCHEMA's own
//     structure — every subschema reachable via properties/items/additionalProperties — regardless
//     of whether the instance being validated visits that branch. This is what lets the fail-closed
//     check catch an unrecognized keyword sitting on a dormant/unvisited subschema.

export const ALLOWED_SCHEMA_KEYWORDS = new Set([
  '$schema',
  '$comment',
  'title',
  'description',
  'type',
  'additionalProperties',
  'required',
  'properties',
  'const',
  'enum',
  'pattern',
  'items',
  'minItems',
]);

export class SchemaEvaluationError extends Error {}

function isPlainObject(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isSchemaObject(value) {
  return isPlainObject(value);
}

/**
 * Recursively walk every subschema reachable from `schema` via `properties`, `items`, and a
 * schema-valued `additionalProperties` — unconditionally, not gated on what the instance visits —
 * and collect any key outside ALLOWED_SCHEMA_KEYWORDS.
 * @param {object} schema
 * @param {string} [path]
 * @returns {Array<{ path: string, keyword: string }>}
 */
export function collectUnknownKeywords(schema, path = '$') {
  const violations = [];
  if (!isSchemaObject(schema)) return violations;

  for (const key of Object.keys(schema)) {
    if (!ALLOWED_SCHEMA_KEYWORDS.has(key)) {
      violations.push({ path, keyword: key });
    }
  }

  if (isPlainObject(schema.properties)) {
    for (const propName of Object.keys(schema.properties)) {
      violations.push(
        ...collectUnknownKeywords(schema.properties[propName], `${path}.properties.${propName}`),
      );
    }
  }
  if (isSchemaObject(schema.items)) {
    violations.push(...collectUnknownKeywords(schema.items, `${path}.items`));
  }
  if (isSchemaObject(schema.additionalProperties)) {
    violations.push(
      ...collectUnknownKeywords(schema.additionalProperties, `${path}.additionalProperties`),
    );
  }
  return violations;
}

/** Throws SchemaEvaluationError if collectUnknownKeywords finds anything. */
export function assertNoUnknownKeywords(schema) {
  const violations = collectUnknownKeywords(schema);
  if (violations.length > 0) {
    const detail = violations.map((v) => `${v.path}: unrecognized keyword '${v.keyword}'`).join('; ');
    throw new SchemaEvaluationError(`Unrecognized schema keyword(s): ${detail}`);
  }
}

/**
 * Enumerate every subschema node reachable from `schema` (same traversal as
 * collectUnknownKeywords), returning navigation `steps` that can relocate the identical node inside
 * a structurally-cloned copy of the same root schema (e.g. via getNodeAtSteps below). Used by the
 * generated fail-closed sweep to mutate one node at a time without hand-listing paths.
 * @param {object} schema
 * @param {Array<{via: 'properties'|'items'|'additionalProperties', key?: string}>} [steps]
 * @param {string} [path]
 * @returns {Array<{ path: string, steps: Array<object> }>}
 */
export function enumerateSchemaNodes(schema, steps = [], path = '$') {
  const nodes = [];
  if (!isSchemaObject(schema)) return nodes;

  nodes.push({ path, steps });

  if (isPlainObject(schema.properties)) {
    for (const propName of Object.keys(schema.properties)) {
      nodes.push(
        ...enumerateSchemaNodes(
          schema.properties[propName],
          [...steps, { via: 'properties', key: propName }],
          `${path}.properties.${propName}`,
        ),
      );
    }
  }
  if (isSchemaObject(schema.items)) {
    nodes.push(...enumerateSchemaNodes(schema.items, [...steps, { via: 'items' }], `${path}.items`));
  }
  if (isSchemaObject(schema.additionalProperties)) {
    nodes.push(
      ...enumerateSchemaNodes(
        schema.additionalProperties,
        [...steps, { via: 'additionalProperties' }],
        `${path}.additionalProperties`,
      ),
    );
  }
  return nodes;
}

/** Relocate the node `enumerateSchemaNodes` found at `steps`, inside any structural clone of the same root schema. */
export function getNodeAtSteps(schema, steps) {
  let node = schema;
  for (const step of steps) {
    if (step.via === 'properties') node = node.properties[step.key];
    else if (step.via === 'items') node = node.items;
    else if (step.via === 'additionalProperties') node = node.additionalProperties;
    else throw new SchemaEvaluationError(`getNodeAtSteps: unknown step kind '${step.via}'`);
  }
  return node;
}

function typeMatches(value, type) {
  switch (type) {
    case 'object':
      return isPlainObject(value);
    case 'array':
      return Array.isArray(value);
    case 'string':
      return typeof value === 'string';
    case 'boolean':
      return typeof value === 'boolean';
    case 'null':
      return value === null;
    case 'number':
      return typeof value === 'number';
    case 'integer':
      return typeof value === 'number' && Number.isInteger(value);
    default:
      return false;
  }
}

/**
 * Validate `instance` against `schema`. Returns an array of human-readable error strings (empty
 * means valid). Does NOT check for unrecognized schema keywords — call assertNoUnknownKeywords (or
 * collectUnknownKeywords) separately for that; the two checks are deliberately independent so a
 * caller can run the fail-closed sweep without also needing a conforming instance on hand.
 * @param {*} instance
 * @param {object} schema
 * @param {string} [path]
 * @returns {string[]}
 */
export function validate(instance, schema, path = '$') {
  const errors = [];

  function walk(value, sch, p) {
    if (sch.const !== undefined) {
      if (value !== sch.const) {
        errors.push(`${p}: expected const ${JSON.stringify(sch.const)}, got ${JSON.stringify(value)}`);
      }
    }
    if (sch.enum !== undefined) {
      if (!sch.enum.includes(value)) {
        errors.push(`${p}: value ${JSON.stringify(value)} not in enum ${JSON.stringify(sch.enum)}`);
      }
    }
    if (sch.type !== undefined) {
      const types = Array.isArray(sch.type) ? sch.type : [sch.type];
      if (!types.some((t) => typeMatches(value, t))) {
        errors.push(`${p}: expected type ${JSON.stringify(sch.type)}, got ${JSON.stringify(value)}`);
        return; // wrong shape — nothing structural below is meaningful to check further
      }
    }
    if (sch.pattern !== undefined && typeof value === 'string') {
      const re = new RegExp(sch.pattern);
      if (!re.test(value)) {
        errors.push(`${p}: value ${JSON.stringify(value)} does not match pattern ${sch.pattern}`);
      }
    }
    if (sch.required !== undefined && isPlainObject(value)) {
      for (const key of sch.required) {
        // Object.hasOwn, not `in`: `in` walks the prototype chain, so a required key named
        // 'toString' or 'constructor' would be satisfied by Object.prototype's inherited member
        // even on a genuinely empty instance object.
        if (!Object.hasOwn(value, key)) errors.push(`${p}: missing required key '${key}'`);
      }
    }
    if (isPlainObject(sch.properties) && isPlainObject(value)) {
      for (const propName of Object.keys(sch.properties)) {
        // Same prototype-chain hazard as the required check above — `propName in value` would
        // wrongly descend into an inherited (not own) member for a name like 'toString'.
        if (Object.hasOwn(value, propName)) {
          walk(value[propName], sch.properties[propName], `${p}.${propName}`);
        }
      }
    }
    if (sch.additionalProperties !== undefined && isPlainObject(value)) {
      const known = new Set(Object.keys(sch.properties || {}));
      for (const key of Object.keys(value)) {
        if (known.has(key)) continue;
        if (sch.additionalProperties === false) {
          errors.push(`${p}.${key}: additional property not allowed`);
        } else if (sch.additionalProperties === true) {
          // open — no-op
        } else if (isSchemaObject(sch.additionalProperties)) {
          walk(value[key], sch.additionalProperties, `${p}.${key}`);
        }
      }
    }
    if (sch.items !== undefined && Array.isArray(value)) {
      value.forEach((el, i) => walk(el, sch.items, `${p}[${i}]`));
    }
    if (sch.minItems !== undefined && Array.isArray(value)) {
      if (value.length < sch.minItems) {
        errors.push(`${p}: array length ${value.length} < minItems ${sch.minItems}`);
      }
    }
  }

  walk(instance, schema, path);
  return errors;
}

/** Throws SchemaEvaluationError with every collected message if `validate` finds any error. */
export function assertValid(instance, schema, path = '$') {
  const errors = validate(instance, schema, path);
  if (errors.length > 0) {
    throw new SchemaEvaluationError(`Schema validation failed:\n${errors.join('\n')}`);
  }
}

// ---- CLI: `node profile-schema-evaluator.mjs <schemaPath>` reads a JSON instance from stdin, runs
// the fail-closed structural sweep then the instance validation, and exits 0 only if both pass. This
// is what reference-assets.test.sh's Ruby-loaded-YAML pipeline drives.
async function runCli() {
  const { readFileSync } = await import('node:fs');
  const schemaPath = process.argv[2];
  if (!schemaPath) {
    console.error('usage: node profile-schema-evaluator.mjs <schemaPath> < instance.json');
    process.exitCode = 1;
    return;
  }
  let schema;
  try {
    schema = JSON.parse(readFileSync(schemaPath, 'utf8'));
  } catch (e) {
    console.error(`could not read/parse schema ${schemaPath}: ${e.message}`);
    process.exitCode = 1;
    return;
  }
  let instance;
  try {
    const stdin = readFileSync(0, 'utf8');
    instance = JSON.parse(stdin);
  } catch (e) {
    console.error(`could not read/parse instance JSON from stdin: ${e.message}`);
    process.exitCode = 1;
    return;
  }
  try {
    assertNoUnknownKeywords(schema);
  } catch (e) {
    console.error(e.message);
    process.exitCode = 1;
    return;
  }
  const errors = validate(instance, schema);
  if (errors.length > 0) {
    console.error(errors.join('\n'));
    process.exitCode = 1;
    return;
  }
  console.log('VALID');
}

if (process.argv[1] && process.argv[1].endsWith('profile-schema-evaluator.mjs')) {
  runCli();
}
