/**
 * @typedef {import("../generated/analysis-contract.js").AnalysisRequest} AnalysisRequest
 * @typedef {import("../generated/analysis-contract.js").AnalysisResponse} AnalysisResponse
 * @typedef {import("../generated/analysis-contract.js").ValidationIssue} ValidationIssue
 */

import {
  ANALYSIS_OUTCOMES,
  ANALYSIS_SUCCESS_STATUS,
  RESPONSE_SCHEMAS,
} from "../generated/analysis-contract.js";
import { createSchemaMatcher, isRecord, readErrorEnvelope } from "../core/contract-decode.js";

/**
 * @typedef {object} AnalysisFailure
 * @property {"network" | "timeout" | "validation" | "unavailable" | "unexpected"
 *   | "malformed" | "offline" | "input"} kind
 * @property {number | null} status
 * @property {string | null} detail
 * @property {readonly ValidationIssue[]} issues
 */

/**
 * @typedef {{ ok: true, response: AnalysisResponse }
 *   | { ok: false, failure: AnalysisFailure }} AnalysisOutput
 */

export const DEFAULT_ANALYSIS_ENDPOINT = "/api/analysis";
export const DEFAULT_TIMEOUT_MS = 15000;

/**
 * @param {AnalysisFailure["kind"]} kind
 * @param {number | null} status
 * @param {string | null} detail
 * @param {readonly ValidationIssue[]} [issues]
 * @returns {{ ok: false, failure: AnalysisFailure }}
 */
function failure(kind, status, detail, issues = []) {
  return { ok: false, failure: { kind, status, detail, issues } };
}

const { matchesSchema } = createSchemaMatcher(RESPONSE_SCHEMAS);

/**
 * Accept a success body only when it is exactly one of the contract results.
 *
 * The report is an auditable record, so a body merely shaped like a result is
 * not enough. The outcome selects its own variant of the generated schema
 * table, and the body is then decoded strictly against it: every declared
 * member present, no member the variant does not declare, closed enums and
 * constants, string and numeric bounds and array bounds down to each element.
 * Anything else is reported as a failure instead of reaching the report with
 * holes, invented labels or values the contract cannot produce.
 *
 * @param {unknown} body
 * @returns {AnalysisResponse | null}
 */
export function readAnalysisResponse(body) {
  if (!isRecord(body) || typeof body.outcome !== "string") {
    return null;
  }
  const contract = ANALYSIS_OUTCOMES.find((entry) => entry.outcome === body.outcome);
  if (contract === undefined || !matchesSchema(body, contract.schema)) {
    return null;
  }
  return /** @type {AnalysisResponse} */ (/** @type {unknown} */ (body));
}

/**
 * Create the single client used by the dashboard to reach `POST /analysis`.
 *
 * @param {object} [options]
 * @param {typeof fetch} [options.fetchImpl]
 * @param {string} [options.endpoint]
 * @param {number} [options.timeoutMs]
 * @returns {{ requestAnalysis: (request: AnalysisRequest) => Promise<AnalysisOutput> }}
 */
export function createAnalysisClient(options = {}) {
  const fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  const endpoint = options.endpoint ?? DEFAULT_ANALYSIS_ENDPOINT;
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;

  /**
   * @param {AnalysisRequest} request
   * @returns {Promise<AnalysisOutput>}
   */
  async function requestAnalysis(request) {
    const controller = new AbortController();
    // One deadline covers the whole exchange. Headers arriving early prove
    // nothing: a body that never ends would hang the panel just as a silent
    // connection would, so the abort stays armed until the body is read.
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      /** @type {Response} */
      let httpResponse;
      try {
        httpResponse = await fetchImpl(endpoint, {
          method: "POST",
          headers: { "content-type": "application/json", accept: "application/json" },
          body: JSON.stringify(request),
          signal: controller.signal,
        });
      } catch {
        return controller.signal.aborted
          ? failure("timeout", null, null)
          : failure("network", null, null);
      }
      if (controller.signal.aborted) {
        return failure("timeout", null, null);
      }

      /** @type {unknown} */
      let body = null;
      let parsed = true;
      try {
        body = await httpResponse.json();
      } catch {
        if (controller.signal.aborted) {
          return failure("timeout", null, null);
        }
        parsed = false;
      }
      if (controller.signal.aborted) {
        return failure("timeout", null, null);
      }

      if (httpResponse.status === ANALYSIS_SUCCESS_STATUS) {
        const response = parsed ? readAnalysisResponse(body) : null;
        return response === null
          ? failure("malformed", httpResponse.status, null)
          : { ok: true, response };
      }
      if (httpResponse.status === 422) {
        const envelope = readErrorEnvelope(body);
        return failure("validation", 422, envelope.detail, envelope.issues);
      }
      if (httpResponse.status === 503) {
        const envelope = readErrorEnvelope(body);
        return failure("unavailable", 503, envelope.detail, envelope.issues);
      }
      if (httpResponse.status === 502 || httpResponse.status === 504) {
        // The page and the API share an origin through the web process, so a
        // gateway status means the API itself was never reached.
        return failure("network", httpResponse.status, null);
      }
      // Every other status is outside the contract, `2xx` included: the v1
      // operation publishes a result only under the success status, so a 201 or
      // a 204 is an API the panel cannot read, not a result to display.
      return failure("unexpected", httpResponse.status, null);
    } finally {
      clearTimeout(timer);
    }
  }

  return { requestAnalysis };
}
