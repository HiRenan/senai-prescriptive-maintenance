import { TOP_K } from "../generated/analysis-contract.js";
import {
  FEATURE_DESCRIPTORS,
  checkBounds,
  toAnalysisFeatures,
  validationIssue,
} from "./features.js";

/**
 * @typedef {import("../generated/analysis-contract.js").AnalysisFeatures} AnalysisFeatures
 * @typedef {import("../generated/analysis-contract.js").AnalysisRequest} AnalysisRequest
 * @typedef {import("./features.js").ValidationIssue} ValidationIssue
 */

/**
 * The request the panel is willing to read, in bytes. It matches the cap the
 * web process applies to `POST /api/analysis`, so nothing that would be
 * refused on the way out is loaded into the page first.
 */
export const MAX_IMPORT_BYTES = 64 * 1024;

/**
 * @param {unknown} value
 * @returns {value is Record<string, unknown>}
 */
function isPlainObject(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Refuse an oversized document before it is read, by the size the source
 * already declares.
 *
 * @param {number} bytes
 * @returns {{ ok: false, issues: readonly ValidationIssue[] } | null}
 */
export function checkImportSize(bytes) {
  if (bytes <= MAX_IMPORT_BYTES) {
    return null;
  }
  const limit = Math.round(MAX_IMPORT_BYTES / 1024);
  return {
    ok: false,
    issues: [
      {
        field: "documento",
        code: "document_too_large",
        message: `O documento excede ${limit} KiB. Importe uma requisição de análise.`,
      },
    ],
  };
}

/**
 * Parse a pasted or uploaded analysis request against the frozen v1 shape.
 *
 * The contract forbids extra properties, so unknown keys are reported instead
 * of being dropped silently: an import that looks accepted must be the payload
 * the API would actually receive.
 *
 * @param {string} text
 * @returns {{ ok: true, request: AnalysisRequest }
 *   | { ok: false, issues: readonly ValidationIssue[] }}
 */
export function importAnalysisRequest(text) {
  const oversized = checkImportSize(new Blob([text]).size);
  if (oversized !== null) {
    return oversized;
  }
  if (text.trim() === "") {
    return {
      ok: false,
      issues: [
        {
          field: "documento",
          code: "empty_document",
          message: "Cole um JSON ou escolha um arquivo antes de importar.",
        },
      ],
    };
  }
  /** @type {unknown} */
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    return {
      ok: false,
      issues: [
        {
          field: "documento",
          code: "invalid_json",
          message: "O conteúdo não é um JSON válido.",
        },
      ],
    };
  }
  if (!isPlainObject(parsed)) {
    return {
      ok: false,
      issues: [
        {
          field: "documento",
          code: "not_an_object",
          message: "O JSON precisa ser um objeto com a chave features.",
        },
      ],
    };
  }

  /** @type {ValidationIssue[]} */
  const issues = [];
  for (const key of Object.keys(parsed)) {
    if (key !== "features" && key !== "top_k") {
      issues.push({
        field: key,
        code: "unexpected_property",
        message: "O contrato v1 não aceita esta chave.",
      });
    }
  }

  const rawFeatures = parsed.features;
  if (!isPlainObject(rawFeatures)) {
    issues.push({
      field: "features",
      code: "missing_features",
      message: "Informe o objeto features com as 18 entradas do contrato.",
    });
    return { ok: false, issues: Object.freeze(issues) };
  }

  const known = new Set(FEATURE_DESCRIPTORS.map((descriptor) => descriptor.name));
  for (const key of Object.keys(rawFeatures)) {
    if (!known.has(/** @type {keyof AnalysisFeatures} */ (key))) {
      issues.push({
        field: key,
        code: "unexpected_feature",
        message: "O contrato v1 não declara esta feature.",
      });
    }
  }

  /** @type {Record<string, number>} */
  const features = {};
  for (const descriptor of FEATURE_DESCRIPTORS) {
    const value = rawFeatures[descriptor.name];
    if (value === undefined) {
      issues.push(validationIssue(descriptor.name, "required"));
      continue;
    }
    if (typeof value !== "number" || !Number.isFinite(value)) {
      issues.push({
        field: descriptor.name,
        code: "not_a_json_number",
        message: "Use um número JSON finito, sem aspas.",
      });
      continue;
    }
    const bounded = checkBounds(descriptor, value);
    if (bounded.ok) {
      features[descriptor.name] = bounded.value;
    } else {
      issues.push(bounded.issue);
    }
  }

  let topK = TOP_K.fallback;
  const rawTopK = parsed.top_k;
  if (rawTopK !== undefined) {
    if (!Number.isInteger(rawTopK)) {
      issues.push(validationIssue("top_k", "not_an_integer"));
    } else {
      const candidate = /** @type {number} */ (rawTopK);
      if (candidate < TOP_K.minimum) {
        issues.push(validationIssue("top_k", "below_minimum", TOP_K.minimum));
      } else if (candidate > TOP_K.maximum) {
        issues.push(validationIssue("top_k", "above_maximum", TOP_K.maximum));
      } else {
        topK = candidate;
      }
    }
  }

  const narrowed = toAnalysisFeatures(features);
  if (issues.length > 0 || narrowed === null) {
    return { ok: false, issues: Object.freeze(issues) };
  }
  return { ok: true, request: { features: narrowed, top_k: topK } };
}
