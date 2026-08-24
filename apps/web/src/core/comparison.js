import { FEATURE_PAIRS, SINGLE_FEATURES, axisLabel } from "./features.js";

/**
 * @typedef {import("../generated/analysis-contract.js").AnalysisFeatures} AnalysisFeatures
 */

export const COMPARISON_DISCLAIMER =
  "Comparação descritiva dos valores enviados nesta execução. Não indica causa, " +
  "gravidade nem relação com o desfecho da análise.";

/**
 * @typedef {object} ComparisonEntry
 * @property {string} name
 * @property {string} axis
 * @property {number} value
 * @property {number} ratio
 * @property {boolean} negative
 */

/**
 * @typedef {object} ComparisonPair
 * @property {string} metric
 * @property {string} label
 * @property {string | null} unit
 * @property {number} scale
 * @property {readonly ComparisonEntry[]} entries
 */

/**
 * @typedef {object} ComparisonReading
 * @property {string} name
 * @property {string} label
 * @property {string | null} unit
 * @property {number} value
 */

/**
 * Describe the submitted features side by side.
 *
 * Bars are scaled inside each metric only, because the 18 features do not share
 * a unit. Nothing here is compared against the model, the neighbours or any
 * threshold, so the panel stays descriptive.
 *
 * @param {AnalysisFeatures} features
 * @returns {{ pairs: readonly ComparisonPair[], readings: readonly ComparisonReading[] }}
 */
export function buildFeatureComparison(features) {
  const pairs = FEATURE_PAIRS.map((pair) => {
    const values = pair.axes.map((descriptor) => features[descriptor.name]);
    const scale = Math.max(...values.map((value) => Math.abs(value)));
    const entries = pair.axes.map((descriptor, index) => {
      const value = values[index];
      return {
        name: descriptor.name,
        axis: descriptor.axis === null ? "" : axisLabel(descriptor.axis),
        value,
        ratio: scale === 0 ? 0 : Math.abs(value) / scale,
        negative: value < 0,
      };
    });
    return {
      metric: pair.metric,
      label: pair.label,
      unit: pair.unit,
      scale,
      entries: Object.freeze(entries),
    };
  });
  const readings = SINGLE_FEATURES.map((descriptor) => ({
    name: descriptor.name,
    label: descriptor.label,
    unit: descriptor.unit,
    value: features[descriptor.name],
  }));
  return { pairs: Object.freeze(pairs), readings: Object.freeze(readings) };
}
