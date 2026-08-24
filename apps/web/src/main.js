import {
  API_CONTRACT_VERSION,
  SYNTHETIC_ANALYSIS_EXAMPLES,
} from "./generated/analysis-contract.js";
import { DOCUMENT_CONTRACT_VERSION } from "./generated/document-contract.js";
import { createAnalysisClient } from "./api/analysis-client.js";
import { createOfflineAnalysisClient } from "./api/offline-analysis-client.js";
import {
  FEATURE_NAMES,
  buildAnalysisRequest,
  normalizeAnalysisField,
  requestToConsoleValues,
} from "./core/features.js";
import { createLatestRequestController } from "./core/latest-request.js";
import { checkImportSize, importAnalysisRequest } from "./core/request-import.js";
import { presentAnalysis, presentFailure } from "./core/presentation.js";
import { createConsoleView } from "./ui/console-view.js";
import { createDocumentsPanel } from "./ui/documents-view.js";
import { createReportView } from "./ui/report-view.js";
import { startWorkspaceNavigation } from "./ui/workspace-navigation.js";
import { clear, el, requireElement } from "./ui/dom.js";

/**
 * @typedef {import("./generated/analysis-contract.js").AnalysisRequest} AnalysisRequest
 * @typedef {import("./api/analysis-client.js").AnalysisOutput} AnalysisOutput
 * @typedef {import("./core/features.js").ValidationIssue} ValidationIssue
 * @typedef {"online" | "offline"} ReportSource
 * @typedef {{ request: AnalysisRequest, source: ReportSource }} AnalysisRun
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
  const onlineMode = requireElement("online-mode");
  const offlineMode = requireElement("offline-mode");
  const modeDescription = requireElement("mode-description");

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
  const source = /** @type {ReportSource} */ (
    new URL(window.location.href).searchParams.get("mode") === "offline"
      ? "offline"
      : "online"
  );
  const offline = source === "offline";
  if (offline) {
    onlineMode.removeAttribute("aria-current");
    offlineMode.setAttribute("aria-current", "page");
  } else {
    onlineMode.setAttribute("aria-current", "page");
    offlineMode.removeAttribute("aria-current");
  }
  modeDescription.textContent = offline
    ? "Offline ativo: somente as cinco fixtures sintéticas do contrato, sem chamadas à API. Entradas alteradas não recebem outcome inventado."
    : "API local ativa: a leitura é enviada pela mesma origem do painel.";
  startWorkspaceNavigation();

  const consoleView = createConsoleView(consoleRoot);
  const reportView = createReportView(reportRoot);
  const client = offline ? createOfflineAnalysisClient() : createAnalysisClient();
  const documentsPanel = createDocumentsPanel(documentsRoot, { offline });

  for (const example of SYNTHETIC_ANALYSIS_EXAMPLES) {
    const label = offline
      ? `${presentAnalysis(example.response).title} · fixture sintética`
      : example.summary;
    exampleSelect.append(el("option", { value: example.name }, [label]));
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

  /** @returns {void} */
  function clearImportInvalid() {
    importText.removeAttribute("aria-invalid");
    importFile.removeAttribute("aria-invalid");
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
    clearImportInvalid();
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
      importText.setAttribute("aria-invalid", "true");
      announce("Importação recusada.");
      importText.focus();
      return;
    }
    loadRequest(imported.request);
    renderImportIssues(importIssues, [], "");
    clearImportInvalid();
    announce("JSON importado. Revise os valores e execute a análise.");
  }

  requireElement("import-apply").addEventListener("click", () => {
    applyImport(importText.value);
  });

  importFile.addEventListener("change", () => {
    importFile.removeAttribute("aria-invalid");
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
      importFile.setAttribute("aria-invalid", "true");
      announce("Importação recusada.");
      importFile.value = "";
      importFile.focus();
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
        importFile.setAttribute("aria-invalid", "true");
        announce("Importação recusada.");
        importFile.focus();
      });
  });

  importText.addEventListener("input", () => {
    importText.removeAttribute("aria-invalid");
    renderImportIssues(importIssues, [], "");
  });

  requireElement("console-reset").addEventListener("click", () => {
    consoleView.reset();
    exampleSelect.value = "";
    importText.value = "";
    importFile.value = "";
    renderImportIssues(importIssues, [], "");
    clearImportInvalid();
    reportView.showIdle();
    announce("Console limpo.");
  });

  const requests = createLatestRequestController({
    /** @param {AnalysisRun} run */
    onStart(run) {
      consoleView.setBusy(true);
      reportView.showLoading(run.source);
      announce(
        run.source === "offline"
          ? "Preparando o resultado da fixture sintética, sem chamada de rede."
          : "Analisando a leitura enviada.",
      );
    },
    /**
     * @param {AnalysisOutput} output
     * @param {AnalysisRun} run
     */
    onApply(output, run) {
      if (output.ok) {
        const report = presentAnalysis(output.response);
        reportView.showReport(report, run.request.features, run.source);
        announce(
          `${run.source === "offline" ? "Modo offline. " : ""}${report.title}. ${report.nextStep}`,
        );
        reportView.focus();
        return;
      }

      const report = presentFailure(output.failure);
      /** @type {ValidationIssue[]} */
      const fieldIssues = output.failure.issues
        .map((issue) => ({
          field: normalizeAnalysisField(issue.field),
          code: issue.code,
          message: `A API recusou este valor (${issue.code}). Revise o campo.`,
        }))
        .filter(
          (issue) =>
            issue.field === "top_k" ||
            FEATURE_NAMES.includes(
              /** @type {import("./core/features.js").FeatureName} */ (issue.field),
            ),
        );

      if (output.failure.kind === "validation" && fieldIssues.length > 0) {
        const first = /** @type {ValidationIssue} */ (fieldIssues[0]);
        reportView.showFailure(report, run.source, {
          label: "Revisar campos",
          run: () => {
            consoleView.focusField(first.field);
          },
        });
        consoleView.showIssues(fieldIssues);
      } else if (output.failure.kind === "offline") {
        reportView.showFailure(report, run.source, {
          label: "Escolher fixture offline",
          run: () => exampleSelect.focus(),
        });
        reportView.focus();
      } else {
        reportView.showFailure(report, run.source, {
          label: "Tentar novamente",
          run: () => form.requestSubmit(),
        });
        reportView.focus();
      }
      announce(`Nenhum resultado novo foi aplicado. ${report.title}. ${report.nextStep}`);
    },
    onFinish() {
      consoleView.setBusy(false);
    },
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const built = buildAnalysisRequest(consoleView.readFeatures(), consoleView.readTopK());
    if (!built.ok) {
      const first = built.issues[0];
      const report = presentFailure({
        kind: "input",
        status: null,
        detail: null,
        issues: built.issues,
      });
      reportView.showFailure(
        report,
        source,
        first === undefined
          ? null
          : {
              label: "Revisar campos",
              run: () => consoleView.focusField(first.field),
            },
      );
      consoleView.showIssues(built.issues);
      announce(
        `A análise não foi enviada: ${built.issues.length} campo(s) precisam de correção. O último resultado válido, quando existente, foi preservado.`,
      );
      return;
    }
    consoleView.clearIssues();
    void requests.run(
      () => client.requestAnalysis(built.request),
      { request: built.request, source },
    );
  });

  exampleSelect.value = "";
  announce(
    offline
      ? `Modo offline pronto. Escolha ${EXAMPLE_PLACEHOLDER}; nenhuma chamada à API será feita.`
      : `Pronto. Escolha ${EXAMPLE_PLACEHOLDER} ou preencha as 18 features.`,
  );
}

startDashboard();
