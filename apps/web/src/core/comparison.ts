import { FEATURE_PAIRS, SINGLE_FEATURES, axisLabel } from "./features";
import type { AnalysisFeatures } from "../generated/analysis-contract.js";

export const COMPARISON_DISCLAIMER =
  "Comparação descritiva dos valores enviados nesta execução. Não indica causa, " +
  "gravidade nem relação com o desfecho da análise.";

export interface ComparisonEntry {
  name: string;
  axis: string;
  value: number;
  ratio: number;
  negative: boolean;
}

export interface ComparisonPair {
  metric: string;
  label: string;
  unit: string | null;
  scale: number;
  entries: readonly ComparisonEntry[];
}

export interface ComparisonReading {
  name: string;
  label: string;
  unit: string | null;
  value: number;
}

/**
 * Describe the submitted features side by side.
 *
 * Bars are scaled inside each metric only, because the 18 features do not share
 * a unit. Nothing here is compared against the model, the neighbours or any
 * threshold, so the panel stays descriptive.
 */
export function buildFeatureComparison(features: AnalysisFeatures): {
  pairs: readonly ComparisonPair[];
  readings: readonly ComparisonReading[];
} {
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
