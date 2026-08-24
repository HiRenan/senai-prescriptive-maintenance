import { TOP_K } from "../generated/analysis-contract.js";
import {
  FEATURE_DESCRIPTORS,
  FEATURE_PAIRS,
  SINGLE_FEATURES,
  axisLabel,
} from "../core/features.js";
import { clear, el } from "./dom.js";

/**
 * @typedef {import("../core/features.js").FeatureDescriptor} FeatureDescriptor
 * @typedef {import("../core/features.js").ValidationIssue} ValidationIssue
 */

/**
 * @typedef {object} ConsoleView
 * @property {() => Record<string, string>} readFeatures
 * @property {() => string} readTopK
 * @property {(values: Readonly<Record<string, string>>, topK: string) => void} write
 * @property {() => void} reset
 * @property {(issues: readonly ValidationIssue[]) => void} showIssues
 * @property {() => void} clearIssues
 * @property {(busy: boolean) => void} setBusy
 */

/**
 * @param {FeatureDescriptor} descriptor
 * @param {string} inputId
 * @returns {HTMLElement}
 */
function fieldControl(descriptor, inputId) {
  const errorId = `${inputId}-error`;
  const input = el("input", {
    id: inputId,
    name: descriptor.name,
    class: "field-input",
    type: "text",
    inputmode: "decimal",
    autocomplete: "off",
    spellcheck: "false",
    "aria-describedby": errorId,
    "data-feature": descriptor.name,
  });
  return el("div", { class: "field" }, [
    el("label", { class: "field-label", for: inputId }, [
      descriptor.axis === null ? descriptor.label : axisLabel(descriptor.axis),
    ]),
    input,
    el("p", { class: "field-error", id: errorId, "data-error-for": descriptor.name }, []),
  ]);
}

/**
 * Render the 18 contract features grouped by metric, with the axis pair side
 * by side so a reading is typed the way it is measured.
 *
 * @param {HTMLElement} host
 * @returns {void}
 */
function renderFields(host) {
  clear(host);
  for (const pair of FEATURE_PAIRS) {
    host.append(
      el("div", { class: "metric" }, [
        el("div", { class: "metric-head" }, [
          el("h3", { class: "metric-title" }, [pair.label]),
          pair.unit === null ? null : el("span", { class: "metric-unit" }, [pair.unit]),
        ]),
        el(
          "div",
          { class: "metric-fields" },
          pair.axes.map((descriptor) =>
            fieldControl(descriptor, `feature-${descriptor.name}`),
          ),
        ),
      ]),
    );
  }
  host.append(
    el("div", { class: "metric metric-singles" }, [
      el("div", { class: "metric-head" }, [
        el("h3", { class: "metric-title" }, ["Condição do processo"]),
      ]),
      el(
        "div",
        { class: "metric-fields" },
        SINGLE_FEATURES.map((descriptor) =>
          el("div", { class: "field" }, [
            el("label", { class: "field-label", for: `feature-${descriptor.name}` }, [
              descriptor.unit === null
                ? descriptor.label
                : `${descriptor.label} (${descriptor.unit})`,
            ]),
            el("input", {
              id: `feature-${descriptor.name}`,
              name: descriptor.name,
              class: "field-input",
              type: "text",
              inputmode: "decimal",
              autocomplete: "off",
              spellcheck: "false",
              "aria-describedby": `feature-${descriptor.name}-error`,
              "data-feature": descriptor.name,
            }),
            el(
              "p",
              {
                class: "field-error",
                id: `feature-${descriptor.name}-error`,
                "data-error-for": descriptor.name,
              },
              [],
            ),
          ]),
        ),
      ),
    ]),
  );
}

/**
 * Wire the input console: the 18 features, the neighbour count and the
 * per-field error slots.
 *
 * @param {HTMLElement} root
 * @returns {ConsoleView}
 */
export function createConsoleView(root) {
  const fieldsHost = root.querySelector(".metrics");
  if (!(fieldsHost instanceof HTMLElement)) {
    throw new Error("O console não declara o contêiner .metrics.");
  }
  renderFields(fieldsHost);

  const topKInput = root.querySelector("#top-k");
  if (!(topKInput instanceof HTMLInputElement)) {
    throw new Error("O console não declara o campo #top-k.");
  }
  topKInput.min = String(TOP_K.minimum);
  topKInput.max = String(TOP_K.maximum);
  topKInput.value = String(TOP_K.fallback);

  /** @type {Map<string, HTMLInputElement>} */
  const inputs = new Map();
  for (const descriptor of FEATURE_DESCRIPTORS) {
    const input = root.querySelector(`[data-feature="${descriptor.name}"]`);
    if (!(input instanceof HTMLInputElement)) {
      throw new Error(`O campo ${descriptor.name} não foi renderizado.`);
    }
    inputs.set(descriptor.name, input);
  }

  /**
   * @param {string} field
   * @returns {HTMLElement | null}
   */
  function errorSlot(field) {
    const node = root.querySelector(`[data-error-for="${field}"]`);
    return node instanceof HTMLElement ? node : null;
  }

  return {
    readFeatures() {
      /** @type {Record<string, string>} */
      const values = {};
      for (const [name, input] of inputs) {
        values[name] = input.value;
      }
      return values;
    },
    readTopK() {
      return topKInput.value;
    },
    write(values, topK) {
      for (const [name, input] of inputs) {
        input.value = values[name] ?? "";
      }
      topKInput.value = topK;
    },
    reset() {
      for (const input of inputs.values()) {
        input.value = "";
      }
      topKInput.value = String(TOP_K.fallback);
    },
    showIssues(issues) {
      this.clearIssues();
      for (const issue of issues) {
        const slot = errorSlot(issue.field);
        if (slot !== null) {
          slot.textContent = issue.message;
        }
        const input = inputs.get(issue.field);
        if (input !== undefined) {
          input.setAttribute("aria-invalid", "true");
        } else if (issue.field === "top_k") {
          topKInput.setAttribute("aria-invalid", "true");
        }
      }
      const first = issues[0];
      if (first !== undefined) {
        const target = first.field === "top_k" ? topKInput : inputs.get(first.field);
        target?.focus();
      }
    },
    clearIssues() {
      for (const slot of root.querySelectorAll("[data-error-for]")) {
        slot.textContent = "";
      }
      for (const input of inputs.values()) {
        input.removeAttribute("aria-invalid");
      }
      topKInput.removeAttribute("aria-invalid");
    },
    setBusy(busy) {
      for (const control of root.querySelectorAll("input, button, select, textarea")) {
        if (
          control instanceof HTMLInputElement ||
          control instanceof HTMLButtonElement ||
          control instanceof HTMLSelectElement ||
          control instanceof HTMLTextAreaElement
        ) {
          control.disabled = busy;
        }
      }
    },
  };
}
