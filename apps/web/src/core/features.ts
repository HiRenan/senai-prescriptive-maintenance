import { FEATURE_FIELDS, TOP_K } from "../generated/analysis-contract.js";
import type {
  AnalysisFeatures,
  AnalysisRequest,
} from "../generated/analysis-contract.js";

export type FeatureName = keyof AnalysisFeatures;

export interface FeatureDescriptor {
  name: FeatureName;
  metric: string;
  axis: "x" | "z" | null;
  label: string;
  unit: string | null;
  minimum: number | null;
  maximum: number | null;
}

export interface FeaturePair {
  metric: string;
  label: string;
  unit: string | null;
  axes: readonly FeatureDescriptor[];
}

export interface ValidationIssue {
  field: string;
  code: string;
  message: string;
}

// Display labels live here because the frozen contract publishes machine names
// only. Every contract feature must resolve, which the essential tests assert.
const METRIC_LABELS = Object.freeze({
  rms_velocity_mm_s: "Velocidade RMS",
  peak_velocity_mm_s: "Velocidade de pico",
  peak_acceleration_g: "Aceleração de pico",
  rms_acceleration_g: "Aceleração RMS",
  high_freq_rms_accel_g: "Aceleração RMS em alta frequência",
  peak_vel_comp_freq_hz: "Frequência da componente de pico",
  kurtosis: "Curtose",
  crest_factor: "Fator de crista",
  temperature_c: "Temperatura",
  rpm: "Rotação",
});

const METRIC_UNITS = Object.freeze({
  rms_velocity_mm_s: "mm/s",
  peak_velocity_mm_s: "mm/s",
  peak_acceleration_g: "g",
  rms_acceleration_g: "g",
  high_freq_rms_accel_g: "g",
  peak_vel_comp_freq_hz: "Hz",
  kurtosis: null,
  crest_factor: null,
  temperature_c: "°C",
  rpm: "rpm",
});

const AXIS_LABELS = Object.freeze({ x: "Eixo X", z: "Eixo Z" });

// Number() accepts hexadecimal, Infinity and padded input by design. The
// console only accepts a plain decimal literal so pasted junk fails loudly.
const DECIMAL_PATTERN = /^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$/;

function issueMessage(code: string, bound: number | null): string {
  switch (code) {
    case "required":
      return "Informe um valor.";
    case "not_a_number":
      return "Use apenas números. Exemplo: 1,2 ou 1.2.";
    case "not_finite":
      return "Use um número finito.";
    case "not_an_integer":
      return "Use um número inteiro.";
    case "below_minimum":
      return `O mínimo aceito é ${bound}.`;
    case "above_maximum":
      return `O máximo aceito é ${bound}.`;
    default:
      return "Valor inválido.";
  }
}

export function validationIssue(
  field: string,
  code: string,
  bound: number | null = null,
): ValidationIssue {
  return { field, code, message: issueMessage(code, bound) };
}

function describe(name: FeatureName): FeatureDescriptor {
  const field = FEATURE_FIELDS.find((candidate) => candidate.name === name);
  if (field === undefined) {
    throw new Error(`A feature ${name} não existe no contrato v1.`);
  }
  let axis: "x" | "z" | null = null;
  if (name.startsWith("x_")) {
    axis = "x";
  } else if (name.startsWith("z_")) {
    axis = "z";
  }
  const metric = axis === null ? name : name.slice(2);
  const key = metric as keyof typeof METRIC_LABELS;
  const label: string | undefined = METRIC_LABELS[key];
  if (label === undefined) {
    throw new Error(`A métrica ${metric} não tem rótulo declarado.`);
  }
  return {
    name: field.name,
    metric,
    axis,
    label,
    unit: METRIC_UNITS[key],
    minimum: field.minimum,
    maximum: field.maximum,
  };
}

export const FEATURE_DESCRIPTORS: readonly FeatureDescriptor[] = Object.freeze(
  FEATURE_FIELDS.map((field) => describe(field.name)),
);

export const FEATURE_NAMES: readonly FeatureName[] = Object.freeze(
  FEATURE_DESCRIPTORS.map((descriptor) => descriptor.name),
);

/**
 * Group contract features by metric so the console mirrors the sensor layout
 * instead of the wire order.
 */
function groupDescriptors(): {
  pairs: readonly FeaturePair[];
  singles: readonly FeatureDescriptor[];
} {
  const metrics: string[] = [];
  const singles: FeatureDescriptor[] = [];
  for (const descriptor of FEATURE_DESCRIPTORS) {
    if (descriptor.axis === null) {
      singles.push(descriptor);
    } else if (!metrics.includes(descriptor.metric)) {
      metrics.push(descriptor.metric);
    }
  }
  const pairs = metrics.map((metric) => {
    const onX = FEATURE_DESCRIPTORS.filter(
      (entry) => entry.metric === metric && entry.axis === "x",
    );
    const onZ = FEATURE_DESCRIPTORS.filter(
      (entry) => entry.metric === metric && entry.axis === "z",
    );
    const axes = Object.freeze([...onX, ...onZ]);
    return { metric, label: axes[0].label, unit: axes[0].unit, axes };
  });
  return { pairs: Object.freeze(pairs), singles: Object.freeze(singles) };
}

const grouped = groupDescriptors();

export const FEATURE_PAIRS: readonly FeaturePair[] = grouped.pairs;

export const SINGLE_FEATURES: readonly FeatureDescriptor[] = grouped.singles;

export function axisLabel(axis: "x" | "z"): string {
  return AXIS_LABELS[axis];
}

/**
 * Resolve the display label used by field errors and imported payload issues.
 */
export function fieldLabel(name: string): string {
  const normalized = normalizeAnalysisField(name);
  if (normalized === "top_k") {
    return "Vizinhos solicitados";
  }
  const descriptor = FEATURE_DESCRIPTORS.find((entry) => entry.name === normalized);
  if (descriptor === undefined) {
    return name;
  }
  if (descriptor.axis === null) {
    return descriptor.label;
  }
  return `${descriptor.label} (${axisLabel(descriptor.axis)})`;
}

/**
 * The API may identify a feature as `features.rpm`, while the local form owns
 * the control as `rpm`. Normalize only that published container prefix.
 */
export function normalizeAnalysisField(name: string): string {
  return name.startsWith("features.") ? name.slice("features.".length) : name;
}

/**
 * Parse one typed feature value, accepting the decimal comma used locally.
 */
export function parseFeatureValue(
  descriptor: FeatureDescriptor,
  rawValue: string,
): { ok: true; value: number } | { ok: false; issue: ValidationIssue } {
  const trimmed = rawValue.trim();
  if (trimmed === "") {
    return { ok: false, issue: validationIssue(descriptor.name, "required") };
  }
  const normalized =
    trimmed.includes(",") && !trimmed.includes(".")
      ? trimmed.replace(",", ".")
      : trimmed;
  if (!DECIMAL_PATTERN.test(normalized)) {
    return { ok: false, issue: validationIssue(descriptor.name, "not_a_number") };
  }
  const value = Number(normalized);
  if (!Number.isFinite(value)) {
    return { ok: false, issue: validationIssue(descriptor.name, "not_finite") };
  }
  return checkBounds(descriptor, value);
}

export function checkBounds(
  descriptor: FeatureDescriptor,
  value: number,
): { ok: true; value: number } | { ok: false; issue: ValidationIssue } {
  if (descriptor.minimum !== null && value < descriptor.minimum) {
    return {
      ok: false,
      issue: validationIssue(descriptor.name, "below_minimum", descriptor.minimum),
    };
  }
  if (descriptor.maximum !== null && value > descriptor.maximum) {
    return {
      ok: false,
      issue: validationIssue(descriptor.name, "above_maximum", descriptor.maximum),
    };
  }
  return { ok: true, value };
}

export function parseTopK(
  rawValue: string,
): { ok: true; value: number } | { ok: false; issue: ValidationIssue } {
  const trimmed = rawValue.trim();
  if (trimmed === "") {
    return { ok: false, issue: validationIssue("top_k", "required") };
  }
  if (!/^\d+$/.test(trimmed)) {
    return { ok: false, issue: validationIssue("top_k", "not_an_integer") };
  }
  const value = Number(trimmed);
  if (value < TOP_K.minimum) {
    return { ok: false, issue: validationIssue("top_k", "below_minimum", TOP_K.minimum) };
  }
  if (value > TOP_K.maximum) {
    return { ok: false, issue: validationIssue("top_k", "above_maximum", TOP_K.maximum) };
  }
  return { ok: true, value };
}

/**
 * Narrow a fully populated value map to the contract feature object.
 */
export function toAnalysisFeatures(
  values: Readonly<Record<string, number>>,
): AnalysisFeatures | null {
  for (const descriptor of FEATURE_DESCRIPTORS) {
    if (typeof values[descriptor.name] !== "number") {
      return null;
    }
  }
  return values as unknown as AnalysisFeatures;
}

/**
 * Build the request body from raw console input, in contract field order.
 */
export function buildAnalysisRequest(
  rawFeatures: Readonly<Record<string, string>>,
  rawTopK: string,
): { ok: true; request: AnalysisRequest } | { ok: false; issues: readonly ValidationIssue[] } {
  const issues: ValidationIssue[] = [];
  const features: Record<string, number> = {};
  for (const descriptor of FEATURE_DESCRIPTORS) {
    const parsed = parseFeatureValue(descriptor, rawFeatures[descriptor.name] ?? "");
    if (parsed.ok) {
      features[descriptor.name] = parsed.value;
    } else {
      issues.push(parsed.issue);
    }
  }
  const topK = parseTopK(rawTopK);
  if (!topK.ok) {
    issues.push(topK.issue);
  }
  const narrowed = toAnalysisFeatures(features);
  if (issues.length > 0 || narrowed === null || !topK.ok) {
    return { ok: false, issues: Object.freeze(issues) };
  }
  return { ok: true, request: { features: narrowed, top_k: topK.value } };
}

/**
 * Convert a contract request into the string values the console renders.
 */
export function requestToConsoleValues(request: AnalysisRequest): {
  features: Record<string, string>;
  topK: string;
} {
  const features: Record<string, string> = {};
  for (const descriptor of FEATURE_DESCRIPTORS) {
    features[descriptor.name] = String(request.features[descriptor.name]);
  }
  return { features, topK: String(request.top_k ?? TOP_K.fallback) };
}
