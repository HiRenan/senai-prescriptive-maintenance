import {
  API_CONTRACT_VERSION,
  SYNTHETIC_ANALYSIS_EXAMPLES,
} from "./generated/analysis-contract.js";
import { DOCUMENT_CONTRACT_VERSION } from "./generated/document-contract.js";
import { createAnalysisClient } from "./api/analysis-client.js";
import { buildAnalysisRequest, requestToConsoleValues } from "./core/features.js";
import { checkImportSize, importAnalysisRequest } from "./core/request-import.js";
import { presentAnalysis, presentFailure } from "./core/presentation.js";
import { createConsoleView } from "./ui/console-view.js";
import { createDocumentsPanel } from "./ui/documents-view.js";
import { createReportView } from "./ui/report-view.js";
import { startWorkspaceNavigation } from "./ui/workspace-navigation.js";
import { clear, el, requireElement } from "./ui/dom.js";

/**
 * @typedef {import("./generated/analysis-contract.js").AnalysisRequest} AnalysisRequest
 * @typedef {import("./core/features.js").ValidationIssue} ValidationIssue
 */

const EXAMPLE_PLACEHOLDER = "escolha um exemplo sintético";

/**
 * @param {HTMLElement} host
 * @param {readonly ValidationIssue[]} issues
 * @param {string} heading
 * @returns {void}
 */
function renderImportIssues(host, issues, heading) {
  clear(host);
  if (issues.length === 0) {
    host.setAttribute("hidden", "");
    return;
  }
  host.removeAttribute("hidden");
  host.append(
    el("p", { class: "import-issues-heading" }, [heading]),
    el(
      "ul",
      { class: "import-issues-list" },
      issues.map((issue) =>
        el("li", {}, [
          el("span", { class: "mono" }, [issue.field]),
          el("span", {}, [issue.message]),
        ]),
      ),
    ),
  );
}

/**
 * Start the analysis dashboard against the live `POST /analysis` endpoint.
 *
 * @returns {void}
 */
export function startDashboard() {
  const consoleRoot = requireElement("console");
  const reportRoot = requireElement("report");
  const statusRoot = requireElement("run-status");
  const importIssues = requireElement("import-issues");
  const exampleSelect = requireElement("example-select");
  const importText = requireElement("import-text");
  const importFile = requireElement("import-file");
  const form = requireElement("analysis-form");
  const contractLabel = requireElement("contract-version");
  const documentContractLabel = requireElement("document-contract-version");
  const documentsRoot = requireElement("documents-panel");

  if (
    !(exampleSelect instanceof HTMLSelectElement) ||
    !(importText instanceof HTMLTextAreaElement) ||
    !(importFile instanceof HTMLInputElement) ||
    !(form instanceof HTMLFormElement)
  ) {
    throw new Error("O documento não declara os controles esperados do console.");
  }

  contractLabel.textContent = `v${API_CONTRACT_VERSION}`;
  documentContractLabel.textContent = `v${DOCUMENT_CONTRACT_VERSION}`;
  startWorkspaceNavigation();

  const consoleView = createConsoleView(consoleRoot);
  const reportView = createReportView(reportRoot);
  const client = createAnalysisClient();
  const documentsPanel = createDocumentsPanel(documentsRoot);

  for (const example of SYNTHETIC_ANALYSIS_EXAMPLES) {
    exampleSelect.append(el("option", { value: example.name }, [example.summary]));
  }

  reportView.showIdle();
  void documentsPanel.start();

  /**
   * @param {string} message
   * @returns {void}
   */
  function announce(message) {
    statusRoot.textContent = message;
  }

  /**
   * @param {AnalysisRequest} request
   * @returns {void}
   */
  function loadRequest(request) {
    const values = requestToConsoleValues(request);
    consoleView.write(values.features, values.topK);
    consoleView.clearIssues();
  }

  exampleSelect.addEventListener("change", () => {
    const chosen = SYNTHETIC_ANALYSIS_EXAMPLES.find(
      (example) => example.name === exampleSelect.value,
    );
    if (chosen === undefined) {
      return;
    }
    loadRequest(structuredClone(chosen.request));
    renderImportIssues(importIssues, [], "");
    announce(`Exemplo sintético carregado: ${chosen.summary}.`);
  });

  /**
   * @param {string} text
   * @returns {void}
   */
  function applyImport(text) {
    const imported = importAnalysisRequest(text);
    if (!imported.ok) {
      renderImportIssues(
        importIssues,
        imported.issues,
        "A importação foi recusada. Corrija os pontos abaixo e importe de novo.",
      );
      announce("Importação recusada.");
      return;
    }
    loadRequest(imported.request);
    renderImportIssues(importIssues, [], "");
    announce("JSON importado. Revise os valores e execute a análise.");
  }

  requireElement("import-apply").addEventListener("click", () => {
    applyImport(importText.value);
  });

  importFile.addEventListener("change", () => {
    const file = importFile.files?.item(0) ?? null;
    if (file === null) {
      return;
    }
    // Refuse by the declared size first: reading the file is what would spend
    // the memory, so the check has to happen before it, not after.
    const oversized = checkImportSize(file.size);
    if (oversized !== null) {
      renderImportIssues(
        importIssues,
        oversized.issues,
        "A importação foi recusada.",
      );
      announce("Importação recusada.");
      importFile.value = "";
      return;
    }
    file
      .text()
      .then((content) => {
        importText.value = content;
        applyImport(content);
      })
      .catch(() => {
        renderImportIssues(
          importIssues,
          [
            {
              field: "arquivo",
              code: "unreadable_file",
              message: "O arquivo não pôde ser lido. Escolha outro arquivo JSON.",
            },
          ],
          "A importação foi recusada.",
        );
        announce("Importação recusada.");
      });
  });

  requireElement("console-reset").addEventListener("click", () => {
    consoleView.reset();
    exampleSelect.value = "";
    importText.value = "";
    importFile.value = "";
    renderImportIssues(importIssues, [], "");
    reportView.showIdle();
    announce("Console limpo.");
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const built = buildAnalysisRequest(consoleView.readFeatures(), consoleView.readTopK());
    if (!built.ok) {
      consoleView.showIssues(built.issues);
      announce(
        `A análise não foi enviada: ${built.issues.length} campo(s) precisam de correção.`,
      );
      return;
    }
    consoleView.clearIssues();
    consoleView.setBusy(true);
    reportView.showLoading();
    announce("Analisando a leitura enviada.");
    client
      .requestAnalysis(built.request)
      .then((output) => {
        if (output.ok) {
          const report = presentAnalysis(output.response);
          reportView.showReport(report, built.request.features);
          announce(`${report.title}. ${report.nextStep}`);
          return;
        }
        const report = presentFailure(output.failure);
        reportView.showReport(report, built.request.features);
        announce(`${report.title}. ${report.nextStep}`);
      })
      .finally(() => {
        consoleView.setBusy(false);
      });
  });

  exampleSelect.value = "";
  announce(`Pronto. Escolha ${EXAMPLE_PLACEHOLDER} ou preencha as 18 features.`);
}

startDashboard();
