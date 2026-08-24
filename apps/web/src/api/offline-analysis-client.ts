import {
  FEATURE_FIELDS,
  SYNTHETIC_ANALYSIS_EXAMPLES,
  TOP_K,
} from "../generated/analysis-contract.js";
import type { AnalysisRequest } from "../generated/analysis-contract.js";
import { readAnalysisResponse } from "./analysis-client";
import type { AnalysisOutput } from "./analysis-client";

/**
 * Match only an exact contract fixture. The offline path must never infer an
 * outcome for an edited reading because doing so would invent model behaviour.
 */
function matchesFixture(candidate: AnalysisRequest, fixture: AnalysisRequest): boolean {
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
 */
export function createOfflineAnalysisClient(): {
  requestAnalysis: (request: AnalysisRequest) => Promise<AnalysisOutput>;
} {
  return {
    async requestAnalysis(request: AnalysisRequest) {
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
