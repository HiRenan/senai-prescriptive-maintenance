import assert from "node:assert/strict";
import test from "node:test";

import {
  COMPARISON_DISCLAIMER,
  buildFeatureComparison,
} from "../src/core/comparison.js";
import { requestExample } from "./helpers/contract-fixtures.js";

test("a comparação cobre as 18 features enviadas", () => {
  const example = requestExample("normal");
  const comparison = buildFeatureComparison(example.features);
  const covered = [
    ...comparison.pairs.flatMap((pair) => pair.entries.map((entry) => entry.name)),
    ...comparison.readings.map((reading) => reading.name),
  ];
  assert.equal(covered.length, 18);
  assert.deepEqual([...covered].sort(), Object.keys(example.features).sort());
});

test("cada par compara os dois eixos na escala do próprio par", () => {
  const example = requestExample("normal");
  const comparison = buildFeatureComparison(example.features);
  assert.equal(comparison.pairs.length, 8);
  for (const pair of comparison.pairs) {
    assert.equal(pair.entries.length, 2);
    assert.deepEqual(
      pair.entries.map((entry) => entry.axis),
      ["Eixo X", "Eixo Z"],
    );
    const largest = Math.max(...pair.entries.map((entry) => entry.ratio));
    assert.equal(largest, 1, `escala inválida em ${pair.metric}`);
    for (const entry of pair.entries) {
      assert.ok(entry.ratio >= 0 && entry.ratio <= 1);
    }
  }
});

test("um par inteiramente zerado não gera divisão por zero", () => {
  const example = requestExample("normal");
  const features = { ...example.features, x_kurtosis: 0, z_kurtosis: 0 };
  const comparison = buildFeatureComparison(features);
  const pair = comparison.pairs.find((entry) => entry.metric === "kurtosis");
  assert.ok(pair);
  assert.equal(pair.scale, 0);
  assert.deepEqual(
    pair.entries.map((entry) => entry.ratio),
    [0, 0],
  );
});

test("valores negativos são medidos pela magnitude e sinalizados", () => {
  const example = requestExample("normal");
  const features = { ...example.features, x_kurtosis: -6, z_kurtosis: 3 };
  const comparison = buildFeatureComparison(features);
  const pair = comparison.pairs.find((entry) => entry.metric === "kurtosis");
  assert.ok(pair);
  assert.equal(pair.scale, 6);
  assert.deepEqual(
    pair.entries.map((entry) => [entry.ratio, entry.negative]),
    [
      [1, true],
      [0.5, false],
    ],
  );
});

test("temperatura e rotação aparecem como leituras isoladas com unidade", () => {
  const example = requestExample("normal");
  const comparison = buildFeatureComparison(example.features);
  assert.deepEqual(
    comparison.readings.map((reading) => [reading.name, reading.unit]),
    [
      ["temperature_c", "°C"],
      ["rpm", "rpm"],
    ],
  );
});

test("a ressalva nega causalidade explicitamente", () => {
  assert.match(COMPARISON_DISCLAIMER, /Não indica causa/);
  assert.match(COMPARISON_DISCLAIMER, /descritiva/);
});
