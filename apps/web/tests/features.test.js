import assert from "node:assert/strict";
import test from "node:test";

import { TOP_K } from "../src/generated/analysis-contract.js";
import {
  FEATURE_DESCRIPTORS,
  FEATURE_NAMES,
  FEATURE_PAIRS,
  SINGLE_FEATURES,
  buildAnalysisRequest,
  parseFeatureValue,
  parseTopK,
  requestToConsoleValues,
} from "../src/core/features.js";
import { requestExample } from "./helpers/contract-fixtures.js";

/**
 * @param {Record<string, number>} features
 * @returns {Record<string, string>}
 */
function asConsoleValues(features) {
  return Object.fromEntries(
    Object.entries(features).map(([name, value]) => [name, String(value)]),
  );
}

test("toda feature do contrato tem rótulo, eixo e unidade resolvidos", () => {
  assert.equal(FEATURE_DESCRIPTORS.length, 18);
  for (const descriptor of FEATURE_DESCRIPTORS) {
    assert.ok(descriptor.label.length > 0, `sem rótulo: ${descriptor.name}`);
    assert.ok(["x", "z", null].includes(descriptor.axis));
    assert.ok(descriptor.unit === null || descriptor.unit.length > 0);
  }
});

test("o agrupamento cobre as 18 features sem duplicar nem perder nenhuma", () => {
  const grouped = [
    ...FEATURE_PAIRS.flatMap((pair) => pair.axes.map((axis) => axis.name)),
    ...SINGLE_FEATURES.map((descriptor) => descriptor.name),
  ];
  assert.equal(grouped.length, 18);
  assert.deepEqual([...grouped].sort(), [...FEATURE_NAMES].sort());
  assert.equal(FEATURE_PAIRS.length, 8);
  for (const pair of FEATURE_PAIRS) {
    assert.deepEqual(
      pair.axes.map((axis) => axis.axis),
      ["x", "z"],
    );
  }
});

test("valores decimais são aceitos com vírgula ou ponto", () => {
  const descriptor = FEATURE_DESCRIPTORS.find((entry) => entry.name === "rpm");
  assert.ok(descriptor);
  assert.deepEqual(parseFeatureValue(descriptor, "1,5"), { ok: true, value: 1.5 });
  assert.deepEqual(parseFeatureValue(descriptor, " 1.5 "), { ok: true, value: 1.5 });
  assert.deepEqual(parseFeatureValue(descriptor, "-2"), { ok: true, value: -2 });
});

test("entradas que o Number aceitaria por engano são recusadas", () => {
  const descriptor = FEATURE_DESCRIPTORS.find((entry) => entry.name === "rpm");
  assert.ok(descriptor);
  for (const raw of ["0x10", "Infinity", "1 000", "1e", "abc", "1,5.5"]) {
    const parsed = parseFeatureValue(descriptor, raw);
    assert.equal(parsed.ok, false, `deveria recusar: ${raw}`);
  }
});

test("campos vazios e limites do contrato produzem mensagens acionáveis", () => {
  const bounded = FEATURE_DESCRIPTORS.find(
    (entry) => entry.name === "z_rms_velocity_mm_s",
  );
  assert.ok(bounded);
  assert.equal(bounded.minimum, 0);

  const empty = parseFeatureValue(bounded, "");
  assert.equal(empty.ok, false);
  assert.equal(empty.issue.code, "required");
  assert.match(empty.issue.message, /Informe um valor/);

  const negative = parseFeatureValue(bounded, "-1");
  assert.equal(negative.ok, false);
  assert.equal(negative.issue.code, "below_minimum");
  assert.match(negative.issue.message, /mínimo aceito é 0/);
});

test("top_k respeita os limites publicados", () => {
  assert.deepEqual(parseTopK(String(TOP_K.minimum)), { ok: true, value: TOP_K.minimum });
  assert.deepEqual(parseTopK(String(TOP_K.maximum)), { ok: true, value: TOP_K.maximum });
  assert.equal(parseTopK(String(TOP_K.maximum + 1)).ok, false);
  assert.equal(parseTopK("0").ok, false);
  assert.equal(parseTopK("2.5").ok, false);
});

test("uma entrada completa vira uma requisição do contrato", () => {
  const example = requestExample("documented_fault");
  const built = buildAnalysisRequest(asConsoleValues(example.features), "4");
  assert.equal(built.ok, true);
  assert.deepEqual(built.request.features, example.features);
  assert.equal(built.request.top_k, 4);
});

test("uma entrada incompleta reporta um problema por campo e não envia nada", () => {
  const built = buildAnalysisRequest({}, "");
  assert.equal(built.ok, false);
  assert.equal(built.issues.length, 19);
  assert.ok(built.issues.every((issue) => issue.message.length > 0));
});

test("uma requisição volta para o console sem perder valores", () => {
  const example = requestExample("normal");
  const values = requestToConsoleValues(example);
  assert.equal(values.topK, String(example.top_k));
  const rebuilt = buildAnalysisRequest(values.features, values.topK);
  assert.equal(rebuilt.ok, true);
  assert.deepEqual(rebuilt.request.features, example.features);
});
