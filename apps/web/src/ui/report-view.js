import { API_CONTRACT_VERSION } from "../generated/analysis-contract.js";
import { COMPARISON_DISCLAIMER, buildFeatureComparison } from "../core/comparison.js";
import { PRESCRIPTION_STATE } from "../core/presentation.js";
import { formatInstant, formatMeasurement, formatScore, formatWithUnit } from "../core/format.js";
import { clear, el, setCustomProperty } from "./dom.js";
import { toneMark } from "./marks.js";

/**
 * @typedef {import("../core/presentation.js").ReportView} ReportView
 * @typedef {import("../generated/analysis-contract.js").AnalysisFeatures} AnalysisFeatures
 */

/**
 * @param {string} label
 * @param {readonly (Node | string | null)[]} children
 * @param {string} [modifier]
 * @returns {HTMLElement}
 */
function block(label, children, modifier = "") {
  return el("section", { class: `block ${modifier}`.trim() }, [
    el("h3", { class: "block-label" }, [label]),
    ...children,
  ]);
}

/**
 * @param {ReportView} report
 * @returns {HTMLElement}
 */
function masthead(report) {
  const identifiers = report.identifiers.map((entry) =>
    el("div", { class: "identifier" }, [
      el("dt", {}, [entry.label]),
      el("dd", { class: "mono" }, [entry.value]),
    ]),
  );
  identifiers.push(
    el("div", { class: "identifier" }, [
      el("dt", {}, ["Execução local"]),
      el("dd", { class: "mono" }, [formatInstant(new Date())]),
    ]),
  );
  return el("header", { class: "verdict" }, [
    el("p", { class: "verdict-kicker" }, [
      report.kind === "result" ? "Resultado do contrato v1" : "Nenhum resultado obtido",
    ]),
    el("div", { class: "verdict-head" }, [
      el("span", { class: "verdict-mark" }, [toneMark(report.tone)]),
      el("h2", { class: "verdict-title", id: "report-heading" }, [report.title]),
    ]),
    el("p", { class: "verdict-statement" }, [report.statement]),
    el("dl", { class: "identifiers" }, identifiers),
  ]);
}

/**
 * @param {ReportView} report
 * @returns {HTMLElement}
 */
function nextStep(report) {
  return el("div", { class: "next-step" }, [
    el("p", { class: "next-step-label" }, ["Próximo passo"]),
    el("p", { class: "next-step-text" }, [report.nextStep]),
  ]);
}

/**
 * Render the prescription slot. Anything other than an issued prescription is
 * rendered as an explicit void, never as an empty version of a valid one.
 *
 * @param {ReportView} report
 * @returns {HTMLElement}
 */
function prescriptionBlock(report) {
  const prescription = report.prescription;
  if (prescription.state !== PRESCRIPTION_STATE.issued) {
    return block(
      "Prescrição",
      [
        el("div", { class: "prescription-void" }, [
          el("p", { class: "void-heading" }, [prescription.heading]),
          el("p", { class: "void-explanation" }, [prescription.explanation]),
        ]),
      ],
      "prescription",
    );
  }
  return block(
    "Prescrição",
    [
      el("div", { class: "prescription-head" }, [
        el("p", { class: "prescription-heading" }, [prescription.heading]),
        el("span", { class: "priority", "data-priority": prescription.priority }, [
          `Prioridade: ${prescription.priorityLabel}`,
        ]),
      ]),
      el("p", { class: "prescription-summary" }, [prescription.summary]),
      el(
        "ol",
        { class: "actions" },
        prescription.actions.map((action) => el("li", {}, [action])),
      ),
    ],
    "prescription prescription-issued",
  );
}

/**
 * @param {ReportView} report
 * @returns {HTMLElement | null}
 */
function diagnosisBlock(report) {
  if (report.diagnosis === null) {
    return null;
  }
  return block("Diagnóstico", [
    el("p", { class: "diagnosis-summary" }, [report.diagnosis.summary]),
    el("p", { class: "diagnosis-code mono" }, [report.diagnosis.code]),
  ]);
}

/**
 * @param {ReportView} report
 * @returns {HTMLElement | null}
 */
function supportBlock(report) {
  if (report.support === null) {
    return null;
  }
  const gauge = el("div", { class: "gauge" }, [
    el("div", { class: "gauge-fill" }, []),
  ]);
  const fill = gauge.firstElementChild;
  if (fill instanceof HTMLElement) {
    setCustomProperty(fill, "--ratio", String(report.support.score));
  }
  return block("Suporte", [
    el("div", { class: "support-head" }, [
      el("span", { class: "support-level" }, [report.support.label]),
      el("span", { class: "support-score mono" }, [formatScore(report.support.score)]),
    ]),
    gauge,
    el("p", { class: "footnote" }, [report.support.note]),
  ]);
}

/**
 * @param {ReportView} report
 * @returns {HTMLElement | null}
 */
function abstentionBlock(report) {
  if (report.abstention === null) {
    return null;
  }
  return block("Abstenção", [
    el("p", { class: "abstention-label" }, [report.abstention.label]),
    el("p", { class: "abstention-message" }, [report.abstention.message]),
    el("p", { class: "abstention-reason mono" }, [report.abstention.reason]),
  ]);
}

/**
 * @param {ReportView} report
 * @returns {HTMLElement | null}
 */
function citationsBlock(report) {
  if (report.citations.length === 0) {
    return null;
  }
  return block("Citações", [
    el(
      "ul",
      { class: "citations" },
      report.citations.map((citation) =>
        el("li", { class: "citation" }, [
          el("span", { class: "citation-document mono" }, [citation.document_id]),
          el("span", { class: "citation-version mono" }, [citation.document_version]),
          el("span", { class: "citation-chunk mono" }, [citation.chunk]),
          el("span", { class: "citation-page" }, [`página ${citation.page_number}`]),
        ]),
      ),
    ),
  ]);
}

/**
 * @param {ReportView} report
 * @returns {HTMLElement | null}
 */
function neighborsBlock(report) {
  if (report.neighbors.length === 0) {
    return null;
  }
  const widest = Math.max(...report.neighbors.map((neighbor) => neighbor.distance));
  const rows = report.neighbors.map((neighbor) => {
    const bar = el("div", { class: "distance-bar" }, [el("div", { class: "distance-fill" }, [])]);
    const fill = bar.firstElementChild;
    if (fill instanceof HTMLElement) {
      setCustomProperty(fill, "--ratio", String(widest === 0 ? 0 : neighbor.distance / widest));
    }
    return el("tr", {}, [
      el("td", { class: "mono" }, [String(neighbor.rank)]),
      el("td", { class: "mono" }, [neighbor.neighbor_ref]),
      el("td", { class: "mono" }, [neighbor.fault_code]),
      el("td", { class: "distance-cell" }, [
        el("span", { class: "mono" }, [formatMeasurement(neighbor.distance)]),
        bar,
      ]),
    ]);
  });
  return block("Vizinhos opacos", [
    el("div", { class: "table-scroll" }, [
      el("table", { class: "neighbors" }, [
        el("thead", {}, [
          el("tr", {}, [
            el("th", { scope: "col" }, ["#"]),
            el("th", { scope: "col" }, ["Referência"]),
            el("th", { scope: "col" }, ["Código de falha"]),
            el("th", { scope: "col" }, ["Distância"]),
          ]),
        ]),
        el("tbody", {}, rows),
      ]),
    ]),
    el("p", { class: "footnote" }, [report.neighborNote]),
  ]);
}

/**
 * @param {ReportView} report
 * @returns {HTMLElement | null}
 */
function warningsBlock(report) {
  if (report.warnings.length === 0) {
    return null;
  }
  return block("Avisos", [
    el(
      "ul",
      { class: "warnings" },
      report.warnings.map((warning) =>
        el("li", { class: "warning" }, [
          el("span", { class: "warning-code mono" }, [warning.code]),
          el("span", { class: "warning-message" }, [warning.message]),
        ]),
      ),
    ),
  ]);
}

/**
 * @param {ReportView} report
 * @returns {HTMLElement | null}
 */
function issuesBlock(report) {
  if (report.issues.length === 0) {
    return null;
  }
  return block("Campos recusados", [
    el(
      "ul",
      { class: "issues" },
      report.issues.map((issue) =>
        el("li", { class: "issue" }, [
          el("span", { class: "issue-label" }, [issue.label]),
          el("span", { class: "issue-code mono" }, [issue.code]),
        ]),
      ),
    ),
  ]);
}

/**
 * @param {ReportView} report
 * @returns {HTMLElement | null}
 */
function integrityBlock(report) {
  if (report.integrity.length === 0) {
    return null;
  }
  return block("Notas de integridade", [
    el(
      "ul",
      { class: "integrity" },
      report.integrity.map((note) => el("li", {}, [note])),
    ),
  ]);
}

/**
 * @param {AnalysisFeatures} features
 * @returns {HTMLElement}
 */
function comparisonBlock(features) {
  const comparison = buildFeatureComparison(features);
  const pairs = comparison.pairs.map((pair) =>
    el("div", { class: "comparison-metric" }, [
      el("div", { class: "comparison-head" }, [
        el("span", { class: "comparison-label" }, [pair.label]),
        pair.unit === null ? null : el("span", { class: "comparison-unit" }, [pair.unit]),
      ]),
      el(
        "div",
        { class: "comparison-bars" },
        pair.entries.map((entry) => {
          const track = el("div", { class: "comparison-track" }, [
            el("div", { class: "comparison-fill", "data-negative": entry.negative }, []),
          ]);
          const fill = track.firstElementChild;
          if (fill instanceof HTMLElement) {
            setCustomProperty(fill, "--ratio", String(entry.ratio));
          }
          return el("div", { class: "comparison-row" }, [
            el("span", { class: "comparison-axis" }, [entry.axis]),
            track,
            el("span", { class: "comparison-value mono" }, [formatMeasurement(entry.value)]),
          ]);
        }),
      ),
    ]),
  );
  const readings = comparison.readings.map((reading) =>
    el("div", { class: "reading" }, [
      el("dt", {}, [reading.label]),
      el("dd", { class: "mono" }, [formatWithUnit(reading.value, reading.unit)]),
    ]),
  );
  return block("Comparação das features enviadas", [
    el("div", { class: "comparison-grid" }, pairs),
    el("dl", { class: "readings" }, readings),
    el("p", { class: "footnote" }, [COMPARISON_DISCLAIMER]),
  ]);
}

/**
 * @typedef {object} ReportSurface
 * @property {() => void} showIdle
 * @property {() => void} showLoading
 * @property {(report: ReportView, features: AnalysisFeatures | null) => void} showReport
 */

/**
 * @param {HTMLElement} root
 * @returns {ReportSurface}
 */
export function createReportView(root) {
  /**
   * @param {string} tone
   * @param {boolean} busy
   * @returns {void}
   */
  function prepare(tone, busy) {
    clear(root);
    root.setAttribute("data-tone", tone);
    root.setAttribute("aria-busy", busy ? "true" : "false");
  }

  return {
    showIdle() {
      prepare("idle", false);
      root.append(
        el("div", { class: "idle" }, [
          el("p", { class: "idle-kicker" }, [`Contrato v${API_CONTRACT_VERSION}`]),
          el("h2", { class: "idle-title", id: "report-heading" }, [
            "Nenhuma análise executada",
          ]),
          el("p", { class: "idle-text" }, [
            "Preencha as 18 features do contrato ou carregue um exemplo sintético e " +
              "execute a análise. O resultado aparece aqui com diagnóstico, suporte, " +
              "vizinhos, citações e a disponibilidade da prescrição.",
          ]),
        ]),
      );
    },
    showLoading() {
      prepare("idle", true);
      root.append(
        el("div", { class: "loading" }, [
          el("p", { class: "loading-kicker" }, ["Executando"]),
          el("h2", { class: "loading-title", id: "report-heading" }, [
            "Analisando a leitura enviada",
          ]),
          el("div", { class: "skeleton" }, [
            el("span", { class: "skeleton-line skeleton-wide" }, []),
            el("span", { class: "skeleton-line" }, []),
            el("span", { class: "skeleton-line skeleton-short" }, []),
          ]),
        ]),
      );
    },
    showReport(report, features) {
      prepare(report.tone, false);
      const blocks = [
        masthead(report),
        nextStep(report),
        prescriptionBlock(report),
        diagnosisBlock(report),
        supportBlock(report),
        abstentionBlock(report),
        citationsBlock(report),
        neighborsBlock(report),
        warningsBlock(report),
        issuesBlock(report),
        integrityBlock(report),
        features === null ? null : comparisonBlock(features),
      ];
      for (const node of blocks) {
        if (node !== null) {
          root.append(node);
        }
      }
    },
  };
}
