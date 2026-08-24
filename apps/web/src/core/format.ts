const LOCALE = "pt-BR";

const measurementFormatter = new Intl.NumberFormat(LOCALE, {
  maximumFractionDigits: 4,
  minimumFractionDigits: 0,
});

const scoreFormatter = new Intl.NumberFormat(LOCALE, {
  maximumFractionDigits: 2,
  minimumFractionDigits: 2,
});

const timeFormatter = new Intl.DateTimeFormat(LOCALE, {
  dateStyle: "short",
  timeStyle: "medium",
});

/**
 * Format a measured feature or distance for display.
 */
export function formatMeasurement(value: number): string {
  if (!Number.isFinite(value)) {
    return "—";
  }
  return measurementFormatter.format(value);
}

/**
 * Format an uncalibrated support score with a stable two-digit shape.
 */
export function formatScore(value: number): string {
  if (!Number.isFinite(value)) {
    return "—";
  }
  return scoreFormatter.format(value);
}

/**
 * Format the browser-side execution instant shown in the report header.
 */
export function formatInstant(value: Date): string {
  return timeFormatter.format(value);
}

/**
 * Format an instant published by the API, keeping the original text when it
 * cannot be read as a date instead of inventing one.
 */
export function formatTimestamp(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : timeFormatter.format(parsed);
}

/**
 * Join a unit to a value, keeping unitless features clean.
 */
export function formatWithUnit(value: number, unit: string | null): string {
  const measurement = formatMeasurement(value);
  return unit === null ? measurement : `${measurement} ${unit}`;
}
