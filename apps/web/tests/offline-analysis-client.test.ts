import assert from "node:assert/strict";
import { test } from "vitest";

import { createOfflineAnalysisClient } from "../src/api/offline-analysis-client";
import { SYNTHETIC_ANALYSIS_EXAMPLES } from "../src/generated/analysis-contract.js";

test("o cliente offline demonstra exatamente os cinco outcomes do contrato", async () => {
  const client = createOfflineAnalysisClient();
  const outcomes = [];
  for (const fixture of SYNTHETIC_ANALYSIS_EXAMPLES) {
    const output: any = await client.requestAnalysis(structuredClone(fixture.request));
    assert.equal(output.ok, true);
    outcomes.push(output.response.outcome);
    assert.deepEqual(output.response, fixture.response);
  }
  assert.deepEqual(outcomes, [
    "normal",
    "documented_fault",
    "undocumented_fault",
    "out_of_distribution",
    "degraded",
  ]);
});

test("o cliente offline não inventa outcome para uma entrada alterada", async () => {
  const client = createOfflineAnalysisClient();
  const edited: any = structuredClone(SYNTHETIC_ANALYSIS_EXAMPLES[0].request);
  edited.features.rpm += 1;

  const output: any = await client.requestAnalysis(edited);

  assert.equal(output.ok, false);
  assert.equal(output.failure.kind, "offline");
});
