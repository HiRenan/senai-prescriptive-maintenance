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
 *
 * @param {number} value
 * @returns {string}
 */
export function formatMeasurement(value) {
  if (!Number.isFinite(value)) {
    return "—";
  }
  return measurementFormatter.format(value);
}

/**
 * Format an uncalibrated support score with a stable two-digit shape.
 *
 * @param {number} value
 * @returns {string}
 */
export function formatScore(value) {
  if (!Number.isFinite(value)) {
    return "—";
  }
  return scoreFormatter.format(value);
}

/**
 * Format the browser-side execution instant shown in the report header.
 *
 * @param {Date} value
 * @returns {string}
 */
export function formatInstant(value) {
  return timeFormatter.format(value);
}

/**
 * Join a unit to a value, keeping unitless features clean.
 *
 * @param {number} value
 * @param {string | null} unit
 * @returns {string}
 */
export function formatWithUnit(value, unit) {
  const measurement = formatMeasurement(value);
  return unit === null ? measurement : `${measurement} ${unit}`;
}
