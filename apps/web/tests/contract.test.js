import assert from "node:assert/strict";
import test from "node:test";

import {
  ANALYSIS_OUTCOMES,
  ANALYSIS_PATH,
  API_CONTRACT_VERSION,
  FEATURE_FIELDS,
  NEIGHBOR_DISTANCE_NOTE,
  PRESCRIPTION_PRIORITIES,
  SUPPORT_SCORE_NOTE,
  SYNTHETIC_ANALYSIS_EXAMPLES,
  TOP_K,
} from "../src/generated/analysis-contract.js";
import {
  requestExamples,
  responseExamples,
  snapshot,
} from "./helpers/contract-fixtures.js";

const schemas = snapshot.components.schemas;

test("o módulo gerado declara a versão e o caminho do contrato", () => {
  assert.equal(API_CONTRACT_VERSION, snapshot.info.version);
  assert.equal(ANALYSIS_PATH, "/analysis");
  assert.ok(snapshot.paths[ANALYSIS_PATH].post);
});

test("as 18 features vêm do contrato, na ordem e com os limites publicados", () => {
  const properties = schemas.AnalysisFeatures.properties;
  const expected = Object.keys(properties);

  assert.equal(FEATURE_FIELDS.length, 18);
  assert.deepEqual(
    FEATURE_FIELDS.map((field) => field.name),
    expected,
  );
  assert.deepEqual([...schemas.AnalysisFeatures.required].sort(), [...expected].sort());

  for (const field of FEATURE_FIELDS) {
    const declared = properties[field.name];
    assert.equal(field.title, declared.title);
    assert.equal(field.minimum, declared.minimum ?? null);
    assert.equal(field.maximum, declared.maximum ?? null);
  }
});

test("os limites de top_k vêm do contrato", () => {
  const declared = schemas.AnalysisRequest.properties.top_k;
  assert.equal(TOP_K.fallback, declared.default);
  assert.equal(TOP_K.minimum, declared.minimum);
  assert.equal(TOP_K.maximum, declared.maximum);
});

test("os cinco desfechos derivam da união discriminada do contrato", () => {
  const variants = schemas.AnalysisResponse.oneOf.map((entry) =>
    entry.$ref.replace("#/components/schemas/", ""),
  );

  assert.equal(ANALYSIS_OUTCOMES.length, 5);
  assert.deepEqual(
    ANALYSIS_OUTCOMES.map((entry) => entry.schema),
    variants,
  );

  for (const entry of ANALYSIS_OUTCOMES) {
    const variant = schemas[entry.schema].properties;
    assert.equal(entry.outcome, variant.outcome.const);
    assert.equal(entry.hasDiagnosis, variant.diagnosis.type !== "null");
    assert.equal(entry.hasAbstention, variant.abstention.type !== "null");
    assert.equal(entry.prescribes, variant.prescription.type !== "null");
    assert.equal(entry.maxCitations, variant.citations.maxItems);

    const supportSchema = variant.support.$ref.replace("#/components/schemas/", "");
    assert.equal(entry.supportLevel, schemas[supportSchema].properties.level.const);

    if (entry.hasAbstention) {
      const reason = variant.abstention.$ref.replace("#/components/schemas/", "");
      assert.equal(entry.abstentionReason, schemas[reason].properties.reason.const);
    } else {
      assert.equal(entry.abstentionReason, null);
    }
  }
});

test("apenas documented_fault emite prescrição", () => {
  const prescribing = ANALYSIS_OUTCOMES.filter((entry) => entry.prescribes);
  assert.deepEqual(
    prescribing.map((entry) => entry.outcome),
    ["documented_fault"],
  );
});

test("as prioridades de prescrição vêm do enum do contrato", () => {
  assert.deepEqual([...PRESCRIPTION_PRIORITIES], schemas.PrescriptionPriority.enum);
});

test("as ressalvas exibidas são o texto do próprio contrato", () => {
  assert.equal(
    SUPPORT_SCORE_NOTE,
    schemas.SufficientSupport.properties.support_score.description,
  );
  assert.equal(
    NEIGHBOR_DISTANCE_NOTE,
    schemas.OpaqueNeighbor.properties.distance.description,
  );
});

test("os exemplos de importação são exatamente os exemplos sintéticos do contrato", () => {
  const names = Object.keys(requestExamples);
  assert.deepEqual(
    SYNTHETIC_ANALYSIS_EXAMPLES.map((example) => example.name),
    names,
  );
  for (const example of SYNTHETIC_ANALYSIS_EXAMPLES) {
    const declared = requestExamples[example.name];
    assert.equal(example.summary, declared.summary);
    assert.deepEqual(example.request, declared.value);
    assert.deepEqual(example.response, responseExamples[example.name].value);
  }
});
