/**
 * Strict decoding against a generated schema table.
 *
 * The tables come from `scripts/generate_web_contract.py`, so the rules applied
 * here are the contract's own: declared members, closed objects, constants,
 * enums, published patterns and bounds. Nothing in this module knows which
 * operation it is checking.
 *
 * @typedef {{ readonly kind: "null" }
 *   | { readonly kind: "const", readonly value: string }
 *   | {
 *       readonly kind: "string",
 *       readonly minLength: number | null,
 *       readonly maxLength: number | null,
 *       readonly pattern: string | null,
 *     }
 *   | {
 *       readonly kind: "integer" | "number",
 *       readonly minimum: number | null,
 *       readonly maximum: number | null,
 *     }
 *   | {
 *       readonly kind: "array",
 *       readonly items: SchemaNode,
 *       readonly minItems: number | null,
 *       readonly maxItems: number | null,
 *     }
 *   | { readonly kind: "enum", readonly values: readonly string[] }
 *   | { readonly kind: "object", readonly schema: string }
 *   | { readonly kind: "union", readonly options: readonly SchemaNode[] }} SchemaNode
 *
 * @typedef {object} ObjectSchema
 * @property {readonly string[]} required
 * @property {Readonly<Record<string, SchemaNode>>} properties
 *
 * @typedef {object} SchemaMatcher
 * @property {(value: unknown, node: SchemaNode) => boolean} matchesNode
 * @property {(value: unknown, name: string) => boolean} matchesSchema
 *
 * @typedef {import("../generated/analysis-contract.js").ValidationIssue} ValidationIssue
 */

/**
 * @param {unknown} value
 * @returns {value is Record<string, unknown>}
 */
export function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Read the contract error envelope without trusting its shape.
 *
 * @param {unknown} body
 * @returns {{ detail: string | null, issues: readonly ValidationIssue[] }}
 */
export function readErrorEnvelope(body) {
  if (!isRecord(body) || !isRecord(body.error)) {
    return { detail: null, issues: [] };
  }
  const error = body.error;
  const detail = typeof error.message === "string" ? error.message : null;
  const rawIssues = Array.isArray(error.issues) ? error.issues : [];
  const issues = rawIssues.flatMap((entry) => {
    if (!isRecord(entry)) {
      return [];
    }
    const field = entry.field;
    const code = entry.code;
    if (typeof field !== "string" || typeof code !== "string") {
      return [];
    }
    return [{ field, code }];
  });
  return { detail, issues: Object.freeze(issues) };
}

/** @type {Map<string, RegExp>} */
const patterns = new Map();

/**
 * The generator only publishes anchored patterns from a subset both engines
 * read the same way, so the contract text is applied as written.
 *
 * @param {string} value
 * @param {string} pattern
 * @returns {boolean}
 */
function matchesPattern(value, pattern) {
  let expression = patterns.get(pattern);
  if (expression === undefined) {
    expression = new RegExp(pattern);
    patterns.set(pattern, expression);
  }
  return expression.test(value);
}

/**
 * Bind the generic rules to one generated table of closed object schemas.
 *
 * @param {Readonly<Record<string, ObjectSchema>>} schemas
 * @returns {SchemaMatcher}
 */
export function createSchemaMatcher(schemas) {
  /**
   * Check one value against a node of the generated schema table.
   *
   * @param {unknown} value
   * @param {SchemaNode} node
   * @returns {boolean}
   */
  function matchesNode(value, node) {
    switch (node.kind) {
      case "null":
        return value === null;
      case "const":
        return value === node.value;
      case "enum":
        return typeof value === "string" && node.values.includes(value);
      case "string":
        return (
          typeof value === "string" &&
          (node.minLength === null || value.length >= node.minLength) &&
          (node.maxLength === null || value.length <= node.maxLength) &&
          (node.pattern === null || matchesPattern(value, node.pattern))
        );
      case "integer":
      case "number":
        return (
          typeof value === "number" &&
          Number.isFinite(value) &&
          (node.kind === "number" || Number.isInteger(value)) &&
          (node.minimum === null || value >= node.minimum) &&
          (node.maximum === null || value <= node.maximum)
        );
      case "array":
        return (
          Array.isArray(value) &&
          (node.minItems === null || value.length >= node.minItems) &&
          (node.maxItems === null || value.length <= node.maxItems) &&
          value.every((entry) => matchesNode(entry, node.items))
        );
      case "union":
        return node.options.some((option) => matchesNode(value, option));
      case "object":
        return matchesSchema(value, node.schema);
      default:
        return false;
    }
  }

  /**
   * Check one value against a closed object schema: no key the schema does not
   * declare, every present member valid and every required member present.
   *
   * @param {unknown} value
   * @param {string} name
   * @returns {boolean}
   */
  function matchesSchema(value, name) {
    const schema = schemas[name];
    if (schema === undefined || !isRecord(value)) {
      return false;
    }
    for (const key of Object.keys(value)) {
      const node = schema.properties[key];
      if (node === undefined || !matchesNode(value[key], node)) {
        return false;
      }
    }
    return schema.required.every((key) => Object.hasOwn(value, key));
  }

  return { matchesNode, matchesSchema };
}
