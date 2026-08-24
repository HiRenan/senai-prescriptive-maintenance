import {
  FEATURE_FIELDS,
  SYNTHETIC_ANALYSIS_EXAMPLES,
  TOP_K,
} from "../generated/analysis-contract.js";
import { readAnalysisResponse } from "./analysis-client.js";

/**
 * @typedef {import("../generated/analysis-contract.js").AnalysisRequest} AnalysisRequest
 * @typedef {import("./analysis-client.js").AnalysisOutput} AnalysisOutput
 */

/**
 * Match only an exact contract fixture. The offline path must never infer an
 * outcome for an edited reading because doing so would invent model behaviour.
 *
 * @param {AnalysisRequest} candidate
 * @param {AnalysisRequest} fixture
 * @returns {boolean}
 */
function matchesFixture(candidate, fixture) {
  if ((candidate.top_k ?? TOP_K.fallback) !== (fixture.top_k ?? TOP_K.fallback)) {
    return false;
  }
  return FEATURE_FIELDS.every(
    (field) => candidate.features[field.name] === fixture.features[field.name],
  );
}

/**
 * Build the zero-network client used by the explicit offline demonstration.
 * Both sides of every fixture are generated from the frozen OpenAPI examples.
 *
 * @returns {{ requestAnalysis: (request: AnalysisRequest) => Promise<AnalysisOutput> }}
 */
export function createOfflineAnalysisClient() {
  return {
    async requestAnalysis(request) {
      const fixture = SYNTHETIC_ANALYSIS_EXAMPLES.find((example) =>
        matchesFixture(request, example.request),
      );
      if (fixture === undefined) {
        return {
          ok: false,
          failure: {
            kind: "offline",
            status: null,
            detail: null,
            issues: Object.freeze([]),
          },
        };
      }
      const response = readAnalysisResponse(structuredClone(fixture.response));
      return response === null
        ? {
            ok: false,
            failure: {
              kind: "malformed",
              status: null,
              detail: null,
              issues: Object.freeze([]),
            },
          }
        : { ok: true, response };
    },
  };
}
