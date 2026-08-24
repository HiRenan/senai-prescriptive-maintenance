import {
  API_CONTRACT_VERSION,
  SYNTHETIC_ANALYSIS_EXAMPLES,
} from "./generated/analysis-contract.js";
import { DOCUMENT_CONTRACT_VERSION } from "./generated/document-contract.js";
import { createAnalysisClient } from "./api/analysis-client.js";
import { createAuthenticatedFetch } from "./api/authenticated-fetch.js";
import { createDocumentClient } from "./api/document-client.js";
import { createOfflineAnalysisClient } from "./api/offline-analysis-client.js";
import { createCognitoAuth, readAndCleanOAuthCallback } from "./auth/cognito.js";
import { clearPkce } from "./auth/pkce.js";
import { createMemorySession } from "./auth/session.js";
import {
  isPublishedFrontendOrigin,
  loadRuntimeConfig,
} from "./config/runtime-config.js";
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
 * @typedef {ReturnType<typeof readAndCleanOAuthCallback>} OAuthCallback
 */

const EXAMPLE_PLACEHOLDER = "escolha um exemplo sintético";

/**
 * @param {HTMLElement} panel
 * @param {HTMLElement} status
 * @param {HTMLElement} detail
 * @param {HTMLButtonElement} loginButton
 * @param {HTMLButtonElement} logoutButton
 * @param {"authenticated" | "required" | "invalid" | "config"} state
 * @returns {void}
 */
function renderAuthentication(
  panel,
  status,
  detail,
  loginButton,
  logoutButton,
  state,
) {
  panel.removeAttribute("hidden");
  panel.dataset.state = state;
  loginButton.hidden = state === "authenticated" || state === "config";
  logoutButton.hidden = state !== "authenticated";
  if (state === "authenticated") {
    status.textContent = "Sessão autenticada";
    detail.textContent =
      "O access token e o refresh token existem somente na memória desta página; não há renovação automática.";
    return;
  }
  if (state === "config") {
    status.textContent = "Configuração de publicação indisponível";
    detail.textContent =
      "O painel recusou a configuração pública. Publique novamente o runtime config canônico antes de usar a API.";
    return;
  }
  status.textContent = state === "invalid" ? "Callback de login recusado" : "Login necessário";
  detail.textContent =
    state === "invalid"
      ? "O callback expirou, não corresponde ao state iniciado ou foi recusado. Inicie um login novo."
      : "Entre pelo Cognito para usar a API. Nenhuma requisição protegida foi enviada.";
}

/** @returns {AnalysisOutput} */
function blockedAnalysisOutput() {
  return {
    ok: false,
    failure: {
      kind: "authentication",
      status: null,
      detail: null,
      issues: [],
    },
  };
}

/**
 * @param {Iterable<Element>} controls
 * @returns {void}
 */
function disableControls(controls) {
  for (const control of controls) {
    if (
      typeof control === "object" &&
      control !== null &&
      "disabled" in control &&
      typeof control.disabled === "boolean"
    ) {
      control.disabled = true;
    }
  }
}

/**
 * @param {Document} documentImpl
 * @param {boolean} ready
 * @param {boolean} busy
 * @returns {void}
 */
function setProtectedSurfaces(documentImpl, ready, busy) {
  setProtectedSurface(documentImpl, "analysis-form", ready, busy);
  setProtectedSurface(documentImpl, "documents-panel", ready, busy);
}

/**
 * @param {Document} documentImpl
 * @param {"analysis-form" | "documents-panel"} id
 * @param {boolean} ready
 * @param {boolean} busy
 * @returns {void}
 */
function setProtectedSurface(documentImpl, id, ready, busy) {
  const surface = documentImpl.getElementById(id);
  if (surface === null) {
    return;
  }
  surface.setAttribute("aria-busy", busy ? "true" : "false");
  if (ready) {
    surface.removeAttribute("inert");
  } else {
    surface.setAttribute("inert", "");
  }
}

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
 * @param {object} [options]
 * @param {ReturnType<typeof readAndCleanOAuthCallback>} [options.oauthCallback]
 * @returns {Promise<void>}
 */
export async function startDashboard(options = {}) {
  setProtectedSurfaces(document, false, true);
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
  const authPanel = requireElement("auth-panel");
  const authStatus = requireElement("auth-status");
  const authDetail = requireElement("auth-detail");
  const loginButton = requireElement("auth-login");
  const logoutButton = requireElement("auth-logout");

  if (
    !(exampleSelect instanceof HTMLSelectElement) ||
    !(importText instanceof HTMLTextAreaElement) ||
    !(importFile instanceof HTMLInputElement) ||
    !(form instanceof HTMLFormElement) ||
    !(loginButton instanceof HTMLButtonElement) ||
    !(logoutButton instanceof HTMLButtonElement)
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
  const published = isPublishedFrontendOrigin(window.location.origin);
  const local = !published;
  modeDescription.textContent = offline
    ? "Offline ativo: somente as cinco fixtures sintéticas do contrato, sem chamadas à API. Entradas alteradas não recebem outcome inventado."
    : local
      ? "API local ativa: a leitura é enviada pela mesma origem do painel."
      : "API AWS autenticada: somente operações publicadas recebem o bearer em memória.";
  onlineMode.textContent = published ? "API AWS autenticada" : "API local";
  startWorkspaceNavigation();

  const consoleView = createConsoleView(consoleRoot);
  const reportView = createReportView(reportRoot);
  /** @type {ReturnType<typeof createAnalysisClient> | ReturnType<typeof createOfflineAnalysisClient>} */
  let client;
  /** @type {ReturnType<typeof createDocumentsPanel>} */
  let documentsPanel;
  /** @type {(() => Promise<void>) | null} */
  let login = null;
  let runtimeBlocked = false;
  let protectedReady = offline || !published;
  let authenticationInvalid = false;

  if (offline) {
    clearPkce(window.sessionStorage);
    client = createOfflineAnalysisClient();
    documentsPanel = createDocumentsPanel(documentsRoot, { offline: true });
  } else if (!published) {
    clearPkce(window.sessionStorage);
    client = createAnalysisClient();
    documentsPanel = createDocumentsPanel(documentsRoot);
  } else {
    const loaded = await loadRuntimeConfig();
    if (!loaded.ok) {
      clearPkce(window.sessionStorage);
      runtimeBlocked = true;
      client = { requestAnalysis: async () => blockedAnalysisOutput() };
      documentsPanel = {
        async start() {
          clear(documentsRoot);
          documentsRoot.append(
            el("p", { class: "documents-empty" }, [
              "A gestão documental foi bloqueada porque o runtime config público não é válido.",
            ]),
          );
        },
        async refresh() {},
      };
      renderAuthentication(
        authPanel,
        authStatus,
        authDetail,
        loginButton,
        logoutButton,
        "config",
      );
    } else {
      const session = createMemorySession({ clientId: loaded.config.cognito.clientId });
      const auth = createCognitoAuth({
        config: loaded.config.cognito,
        session,
        storage: window.sessionStorage,
      });
      const callback = await auth.handleCallback(
        options.oauthCallback ?? { code: null, error: null, invalid: false, state: null },
      );
      const authenticated = callback.ok && session.isAuthenticated();
      protectedReady = authenticated;
      authenticationInvalid = !callback.ok;
      const showRequired = () => {
        protectedReady = false;
        setProtectedSurfaces(document, false, false);
        renderAuthentication(
          authPanel,
          authStatus,
          authDetail,
          loginButton,
          logoutButton,
          "required",
        );
      };
      const authenticatedFetch = createAuthenticatedFetch({
        apiBaseUrl: loaded.config.apiBaseUrl,
        session,
        onAuthenticationRequired: showRequired,
      });
      client = createAnalysisClient({
        endpoint: `${loaded.config.apiBaseUrl}/analysis`,
        fetchImpl: authenticatedFetch,
      });
      documentsPanel = createDocumentsPanel(documentsRoot, {
        client: createDocumentClient({
          prefix: loaded.config.apiBaseUrl,
          fetchImpl: authenticatedFetch,
        }),
      });
      renderAuthentication(
        authPanel,
        authStatus,
        authDetail,
        loginButton,
        logoutButton,
        authenticated
          ? "authenticated"
          : callback.ok
            ? "required"
            : "invalid",
      );
      let loginPending = false;
      login = async () => {
        if (loginPending) {
          return;
        }
        loginPending = true;
        loginButton.disabled = true;
        loginButton.setAttribute("aria-busy", "true");
        authPanel.setAttribute("aria-busy", "true");
        authStatus.textContent = "Abrindo login seguro";
        try {
          await auth.login();
        } catch {
          loginPending = false;
          loginButton.disabled = false;
          loginButton.removeAttribute("aria-busy");
          authPanel.removeAttribute("aria-busy");
          showRequired();
        }
      };
      loginButton.addEventListener("click", () => void login?.());
      logoutButton.addEventListener("click", () => {
        logoutButton.disabled = true;
        void auth.logout().catch(() => {
          showRequired();
          logoutButton.disabled = false;
        });
      });
    }
  }

  for (const example of SYNTHETIC_ANALYSIS_EXAMPLES) {
    const label = offline
      ? `${presentAnalysis(example.response).title} · fixture sintética`
      : example.summary;
    exampleSelect.append(el("option", { value: example.name }, [label]));
  }

  reportView.showIdle();
  /** @type {Promise<void>} */
  let documentInitialization;
  if (protectedReady) {
    documentInitialization = documentsPanel.start();
  } else if (!runtimeBlocked) {
    clear(documentsRoot);
    documentsRoot.append(
      el("p", { class: "documents-empty" }, [
        "A gestão documental permanece bloqueada até uma autenticação válida.",
      ]),
    );
    documentInitialization = Promise.resolve();
  } else {
    documentInitialization = documentsPanel.start();
  }
  if (runtimeBlocked) {
    disableControls(form.elements);
  }

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
      } else if (output.failure.kind === "authentication" && login !== null) {
        reportView.showFailure(report, run.source, {
          label: "Entrar novamente",
          run: () => void login?.(),
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

  setProtectedSurface(document, "analysis-form", protectedReady, false);
  exampleSelect.value = "";
  announce(
    offline
      ? `Modo offline pronto. Escolha ${EXAMPLE_PLACEHOLDER}; nenhuma chamada à API será feita.`
      : runtimeBlocked
        ? "Painel bloqueado: publique um runtime config válido antes de usar a API."
        : published && !protectedReady
          ? authenticationInvalid
            ? "Painel bloqueado: o callback foi recusado. Inicie um login novo."
            : "Painel protegido: entre pelo Cognito antes de usar a API AWS."
          : `Pronto. Escolha ${EXAMPLE_PLACEHOLDER} ou preencha as 18 features.`,
  );
  await documentInitialization;
  setProtectedSurface(document, "documents-panel", protectedReady, false);
}

/** @param {Document} documentImpl @returns {void} */
function renderBootstrapFailure(documentImpl) {
  setProtectedSurfaces(documentImpl, false, false);
  const authPanel = documentImpl.getElementById("auth-panel");
  const authStatus = documentImpl.getElementById("auth-status");
  const authDetail = documentImpl.getElementById("auth-detail");
  const form = documentImpl.getElementById("analysis-form");
  const documents = documentImpl.getElementById("documents-panel");
  if (authPanel !== null && authStatus !== null && authDetail !== null) {
    authPanel.removeAttribute("hidden");
    authPanel.dataset.state = "config";
    authStatus.textContent = "Painel bloqueado com segurança";
    authDetail.textContent =
      "A inicialização não foi concluída. Recarregue a página; nenhuma operação da API foi enviada.";
  }
  if (form !== null && "elements" in form && form.elements !== null) {
    disableControls(/** @type {Iterable<Element>} */ (form.elements));
  }
  disableControls(
    ["auth-login", "auth-logout"]
      .map((id) => documentImpl.getElementById(id))
      .filter((element) => element !== null),
  );
  if (documents !== null) {
    documents.replaceChildren();
    const message = documentImpl.createElement("p");
    message.className = "documents-empty";
    message.textContent =
      "A gestão documental permaneceu bloqueada porque o painel não iniciou.";
    documents.append(message);
  }
}

/**
 * @param {OAuthCallback} callback
 * @param {object} [options]
 * @param {(options: { oauthCallback: OAuthCallback }) => Promise<void>} [options.start]
 * @param {() => void} [options.onFailure]
 * @param {Document} [options.documentImpl]
 * @returns {Promise<void>}
 */
export async function bootstrapDashboard(callback, options = {}) {
  try {
    await (options.start ?? startDashboard)({ oauthCallback: callback });
  } catch {
    if (options.onFailure !== undefined) {
      options.onFailure();
    } else {
      renderBootstrapFailure(options.documentImpl ?? document);
    }
  }
}

if (typeof window !== "undefined") {
  const initialCallback = readAndCleanOAuthCallback(window.location, window.history);
  void bootstrapDashboard(initialCallback);
}
