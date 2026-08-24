import assert from "node:assert/strict";
import test from "node:test";

import { SYNTHETIC_ANALYSIS_EXAMPLES } from "../src/generated/analysis-contract.js";
import {
  MAX_IMPORT_BYTES,
  checkImportSize,
  importAnalysisRequest,
} from "../src/core/request-import.js";
import { requestExample } from "./helpers/contract-fixtures.js";

test("todo exemplo sintético do contrato importa sem ressalvas", () => {
  for (const example of SYNTHETIC_ANALYSIS_EXAMPLES) {
    const imported = importAnalysisRequest(JSON.stringify(example.request));
    assert.equal(imported.ok, true, `falhou: ${example.name}`);
    assert.deepEqual(imported.request.features, example.request.features);
    assert.equal(imported.request.top_k, example.request.top_k);
  }
});

test("um documento vazio explica o que fazer", () => {
  const imported = importAnalysisRequest("   ");
  assert.equal(imported.ok, false);
  assert.equal(imported.issues[0].code, "empty_document");
  assert.match(imported.issues[0].message, /Cole um JSON/);
});

test("JSON inválido é recusado sem quebrar o painel", () => {
  const imported = importAnalysisRequest("{features:");
  assert.equal(imported.ok, false);
  assert.equal(imported.issues[0].code, "invalid_json");
});

test("um JSON que não é objeto é recusado", () => {
  for (const raw of ["[]", '"texto"', "12"]) {
    const imported = importAnalysisRequest(raw);
    assert.equal(imported.ok, false);
    assert.equal(imported.issues[0].code, "not_an_object");
  }
});

test("features ausentes são apontadas uma a uma", () => {
  const imported = importAnalysisRequest('{"features": {}}');
  assert.equal(imported.ok, false);
  assert.equal(imported.issues.length, 18);
  assert.ok(imported.issues.every((issue) => issue.code === "required"));
});

test("chaves fora do contrato são recusadas em vez de descartadas", () => {
  const example = requestExample("normal");
  const payload = { ...example, unexpected: 1 };
  payload.features = { ...example.features, invented_feature: 2 };

  const imported = importAnalysisRequest(JSON.stringify(payload));
  assert.equal(imported.ok, false);
  const codes = imported.issues.map((issue) => issue.code);
  assert.ok(codes.includes("unexpected_property"));
  assert.ok(codes.includes("unexpected_feature"));
});

test("números em texto e valores fora de faixa são recusados", () => {
  const example = requestExample("normal");
  const payload = structuredClone(example);
  payload.features.rpm = "1000";
  payload.features.z_rms_velocity_mm_s = -1;

  const imported = importAnalysisRequest(JSON.stringify(payload));
  assert.equal(imported.ok, false);
  const byField = new Map(imported.issues.map((issue) => [issue.field, issue.code]));
  assert.equal(byField.get("rpm"), "not_a_json_number");
  assert.equal(byField.get("z_rms_velocity_mm_s"), "below_minimum");
});

test("top_k fora dos limites é recusado e nunca é corrigido em silêncio", () => {
  const example = requestExample("normal");
  const payload = structuredClone(example);
  payload.top_k = 99;

  const imported = importAnalysisRequest(JSON.stringify(payload));
  assert.equal(imported.ok, false);
  assert.equal(imported.issues[0].field, "top_k");
  assert.equal(imported.issues[0].code, "above_maximum");
});

test("top_k ausente assume o padrão publicado", () => {
  const example = requestExample("normal");
  const payload = structuredClone(example);
  delete payload.top_k;

  const imported = importAnalysisRequest(JSON.stringify(payload));
  assert.equal(imported.ok, true);
  assert.equal(imported.request.top_k, 5);
});

test("o tamanho é recusado pela contagem declarada, antes de qualquer leitura", () => {
  assert.equal(checkImportSize(MAX_IMPORT_BYTES), null);

  const refused = checkImportSize(MAX_IMPORT_BYTES + 1);
  assert.notEqual(refused, null);
  assert.equal(refused.ok, false);
  assert.equal(refused.issues[0].field, "documento");
  assert.equal(refused.issues[0].code, "document_too_large");
  assert.match(refused.issues[0].message, /64 KiB/);
});

test("um documento acima do limite é recusado sem tentar interpretá-lo", () => {
  const payload = `{"padding":"${"x".repeat(MAX_IMPORT_BYTES)}"}`;
  const imported = importAnalysisRequest(payload);
  assert.equal(imported.ok, false);
  assert.equal(imported.issues[0].code, "document_too_large");
});
