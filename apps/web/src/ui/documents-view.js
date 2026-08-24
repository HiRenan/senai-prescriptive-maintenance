import { SYNTHETIC_DOCUMENT_EXAMPLES } from "../generated/document-contract.js";
import { createDocumentClient } from "../api/document-client.js";
import {
  REGISTER_INPUTS,
  REGISTER_MEDIA_TYPE,
  REJECT_REASON_FIELD,
  buildRegisterRequest,
  buildRejectReason,
  documentFieldHint,
  documentFieldLabel,
  syntheticRegisterValues,
} from "../core/document-registration.js";
import {
  describeRegistration,
  presentDocument,
  presentDocumentFailure,
  presentDocumentList,
} from "../core/document-presentation.js";
import { clear, el } from "./dom.js";
import { documentMark } from "./document-marks.js";

/**
 * @typedef {import("../core/document-presentation.js").DocumentAction} DocumentAction
 * @typedef {import("../core/document-presentation.js").DocumentView} DocumentView
 * @typedef {import("../core/document-presentation.js").DocumentFailureView} DocumentFailureView
 * @typedef {import("../core/features.js").ValidationIssue} ValidationIssue
 * @typedef {import("../generated/document-contract.js").DocumentResponse} DocumentResponse
 * @typedef {ReturnType<typeof createDocumentClient>} DocumentClient
 * @typedef {{ ok: true } | { ok: false, failure: DocumentFailureView }} ListLoadResult
 */

const LEDE =
  "A API v1 registra metadados de um PDF: nome, tipo, tamanho declarado e SHA-256 " +
  "declarado. Ela não recebe o arquivo, não guarda bytes e não confere o hash. " +
  "Registrar nunca aprova: a decisão é humana e aparece como estado do ciclo.";

const REGISTER_NOTE =
  "Nenhum arquivo é lido nem enviado por esta tela. Informe os metadados ou carregue " +
  "um exemplo sintético do contrato, usado apenas para demonstração offline.";

const EXAMPLE_PLACEHOLDER = "Selecione um exemplo";

/**
 * @param {HTMLElement} node
 * @param {boolean} disabled
 * @returns {void}
 */
function setDisabled(node, disabled) {
  if (disabled) {
    node.setAttribute("disabled", "");
    return;
  }
  node.removeAttribute("disabled");
}

/**
 * Apply the request lock to every current form control, including controls
 * rendered inside document cards and confirmation regions.
 *
 * @param {Element} root
 * @param {boolean} disabled
 * @returns {void}
 */
function setInteractiveDisabled(root, disabled) {
  for (const child of root.children) {
    if (["button", "input", "select", "textarea"].includes(child.localName)) {
      setDisabled(/** @type {HTMLElement} */ (child), disabled);
    }
    setInteractiveDisabled(child, disabled);
  }
}

/**
 * Find the first descendant carrying one attribute value, without a selector
 * engine so the same walk works in the browser and in the tests.
 *
 * @param {Element} root
 * @param {string} attribute
 * @param {string} value
 * @returns {HTMLElement | null}
 */
function findIn(root, attribute, value) {
  for (const child of root.children) {
    if (child.getAttribute(attribute) === value) {
      return /** @type {HTMLElement} */ (child);
    }
    const nested = findIn(child, attribute, value);
    if (nested !== null) {
      return nested;
    }
  }
  return null;
}

/**
 * @param {string} label
 * @param {string} value
 * @param {string | null} note
 * @returns {HTMLElement}
 */
function definition(label, value, note) {
  return el("div", { class: "document-fact" }, [
    el("dt", {}, [label]),
    el("dd", { class: "mono" }, [value]),
    note === null ? null : el("dd", { class: "document-fact-note" }, [note]),
  ]);
}

/**
 * Build the documental panel: registration of metadata, listing of the seven
 * published states, per-document reading and the commands each state accepts.
 *
 * @param {HTMLElement} host
 * @param {object} [options]
 * @param {DocumentClient} [options.client]
 * @returns {{ start: () => Promise<void>, refresh: () => Promise<void> }}
 */
export function createDocumentsPanel(host, options = {}) {
  const client = options.client ?? createDocumentClient();

  /** @type {Map<string, HTMLInputElement>} */
  const registerInputs = new Map();
  /** @type {Map<string, HTMLElement>} */
  const registerErrors = new Map();

  const statusRegion = el("p", {
    class: "status documents-status",
    role: "status",
    "aria-live": "polite",
  });
  const listHost = el("div", { class: "document-list" });
  const feedbackHost = el("div", { class: "document-feedback", hidden: true });
  const countLabel = el("p", { class: "documents-count" });
  const refreshButton = /** @type {HTMLButtonElement} */ (
    el("button", { type: "button", class: "button button-quiet", "data-refresh": "" }, [
      "Atualizar lista",
    ])
  );
  const submitButton = /** @type {HTMLButtonElement} */ (
    el("button", { type: "submit", class: "button button-primary" }, [
      "Registrar metadados",
    ])
  );
  const exampleSelect = /** @type {HTMLSelectElement} */ (
    el("select", { id: "document-example", class: "field-input" }, [
      el("option", { value: "" }, [EXAMPLE_PLACEHOLDER]),
    ])
  );
  const form = /** @type {HTMLFormElement} */ (
    el("form", { id: "document-register-form", novalidate: true })
  );

  /** @type {{ documentId: string, action: DocumentAction } | null} */
  let pending = null;
  /** @type {readonly DocumentResponse[]} */
  let documents = Object.freeze([]);
  /** @type {DocumentFailureView | null} */
  let listFailure = null;
  let listLoading = true;
  let busy = false;

  /**
   * @param {string} message
   * @returns {void}
   */
  function announce(message) {
    statusRegion.textContent = message;
  }

  /**
   * @param {boolean} value
   * @returns {void}
   */
  function setBusy(value) {
    busy = value;
    host.setAttribute("aria-busy", value ? "true" : "false");
    setInteractiveDisabled(host, value);
  }

  /**
   * @param {DocumentFailureView | null} failure
   * @param {string | null} confirmation
   * @returns {void}
   */
  function renderFeedback(failure, confirmation) {
    clear(feedbackHost);
    if (failure === null && confirmation === null) {
      feedbackHost.setAttribute("hidden", "");
      return;
    }
    feedbackHost.removeAttribute("hidden");
    feedbackHost.setAttribute(
      "data-tone",
      failure === null ? "settled" : confirmation === null ? "failed" : "withheld",
    );
    if (confirmation !== null) {
      feedbackHost.append(
        el("div", { class: "document-feedback-confirmation" }, [
          el("p", { class: "document-feedback-title" }, ["Comando confirmado"]),
          el("p", { class: "document-feedback-text" }, [confirmation]),
        ]),
      );
    }
    if (failure !== null) {
      feedbackHost.append(
        el("div", { class: "document-feedback-failure" }, [
          el("p", { class: "document-feedback-title" }, [failure.title]),
          el("p", { class: "document-feedback-text" }, [failure.statement]),
          failure.status === null
            ? null
            : el("p", { class: "document-feedback-status mono" }, [
                `Status HTTP ${String(failure.status)}`,
              ]),
          failure.detail === null
            ? null
            : el("p", { class: "document-feedback-detail" }, [
                `Motivo informado pela API: ${failure.detail}`,
              ]),
          failure.issues.length === 0
            ? null
            : el(
                "ul",
                { class: "issues" },
                failure.issues.map((issue) =>
                  el("li", { class: "issue" }, [
                    el("span", {}, [issue.label]),
                    el("span", { class: "issue-code mono" }, [issue.code]),
                  ]),
                ),
              ),
          el("p", { class: "document-feedback-next" }, [failure.nextStep]),
        ]),
      );
    }
  }

  /**
   * @param {DocumentFailureView} failed
   * @returns {void}
   */
  function reportFailure(failed) {
    renderFeedback(failed, null);
    announce(`${failed.title}. ${failed.nextStep}`);
  }

  /**
   * @param {string} documentId
   * @returns {void}
   */
  function focusCard(documentId) {
    const heading = findIn(listHost, "data-card-heading", documentId);
    (heading ?? refreshButton).focus();
  }

  /**
   * @returns {void}
   */
  function focusPending() {
    if (pending === null) {
      return;
    }
    const attribute = pending.action.needsReason ? "data-reason" : "data-confirm";
    findIn(listHost, attribute, pending.documentId)?.focus();
  }

  /**
   * @param {string} documentId
   * @returns {void}
   */
  function cancelPending(documentId) {
    if (busy) {
      return;
    }
    pending = null;
    renderList();
    announce("Ação cancelada. Nada foi enviado para a API.");
    focusCard(documentId);
  }

  /**
  * @param {DocumentView} view
  * @param {DocumentAction} action
   * @param {HTMLTextAreaElement | null} reasonInput
   * @param {HTMLElement} reasonError
   * @returns {Promise<void>}
   */
  async function commit(view, action, reasonInput, reasonError) {
    let reason = "";
    if (action.needsReason) {
      const parsed = buildRejectReason(reasonInput?.value ?? "");
      if (!parsed.ok) {
        reasonError.textContent = parsed.issue.message;
        reasonInput?.setAttribute("aria-invalid", "true");
        announce(`A rejeição não foi enviada: ${parsed.issue.message}`);
        reasonInput?.focus();
        return;
      }
      reason = parsed.reason;
    }
    reasonError.textContent = "";
    reasonInput?.removeAttribute("aria-invalid");
    setBusy(true);
    announce(`Enviando ${action.label.toLowerCase()} para a API.`);
    try {
      const output =
        action.name === "approve"
          ? await client.approveDocument(view.documentId, null)
          : action.name === "reject"
            ? await client.rejectDocument(view.documentId, reason)
            : await client.reprocessDocument(view.documentId);
      pending = null;
      if (!output.ok) {
        renderList();
        reportFailure(presentDocumentFailure(output.failure));
        return;
      }
      const updated = presentDocument(output.value);
      const confirmation =
        `${action.label} registrada. Estado confirmado pela API: ` +
        `${updated.statusLabel}.`;
      const refreshed = await loadDocuments({ silent: true });
      if (!refreshed.ok) {
        renderFeedback(refreshed.failure, confirmation);
        announce(
          `${action.label} concluída, mas a lista não pôde ser atualizada. ` +
            refreshed.failure.nextStep,
        );
        return;
      }
      renderFeedback(null, confirmation);
      announce(
        `${action.label} concluída. O estado atual de ${updated.filename} é ` +
          `${updated.statusLabel}.`,
      );
    } finally {
      setBusy(false);
      focusCard(view.documentId);
    }
  }

  /**
   * @param {DocumentView} view
   * @returns {Promise<void>}
   */
  async function readOne(view) {
    setBusy(true);
    announce(`Consultando o estado atual de ${view.filename}.`);
    try {
      const output = await client.getDocument(view.documentId);
      if (!output.ok) {
        reportFailure(presentDocumentFailure(output.failure));
        return;
      }
      const fresh = output.value;
      documents = Object.freeze(
        documents.map((entry) =>
          entry.document_id === fresh.document_id ? fresh : entry,
        ),
      );
      const read = presentDocument(fresh);
      renderFeedback(null, `Estado lido pela API: ${read.statusLabel}.`);
      renderList();
      announce(
        `Estado atual de ${read.filename}: ${read.statusLabel}. ${read.currency.label}.`,
      );
    } finally {
      setBusy(false);
      focusCard(view.documentId);
    }
  }

  /**
   * @param {DocumentView} view
   * @param {DocumentAction} action
   * @returns {HTMLElement}
   */
  function confirmationRegion(view, action) {
    const reasonId = `document-reason-${view.documentId}`;
    const reasonError = el("p", {
      class: "field-error",
      id: `${reasonId}-error`,
      "data-error-for": "reason",
    });
    const reason = /** @type {HTMLTextAreaElement} */ (
      el("textarea", {
        id: reasonId,
        class: "field-input",
        rows: 2,
        spellcheck: "false",
        "aria-describedby": `${reasonId}-error`,
        "data-reason": view.documentId,
      })
    );
    const confirmButton = el(
      "button",
      {
        type: "button",
        class: "button button-primary",
        "data-confirm": view.documentId,
        "data-action": action.name,
      },
      [action.confirmLabel],
    );
    const cancelButton = el(
      "button",
      { type: "button", class: "button button-quiet", "data-cancel": view.documentId },
      ["Cancelar"],
    );

    confirmButton.addEventListener("click", () => {
      if (!busy) {
        void commit(view, action, action.needsReason ? reason : null, reasonError);
      }
    });
    cancelButton.addEventListener("click", () => {
      if (!busy) {
        cancelPending(view.documentId);
      }
    });
    reason.addEventListener("input", () => {
      reason.removeAttribute("aria-invalid");
      reasonError.textContent = "";
    });

    const region = el(
      "div",
      {
        class: "document-confirm",
        role: "group",
        "aria-label": `${action.label}: confirmação`,
        "data-confirming": view.documentId,
      },
      [
        el("p", { class: "document-confirm-text" }, [action.confirmation]),
        action.needsReason
          ? el("div", { class: "field" }, [
              el("label", { class: "field-label", for: reasonId }, [
                documentFieldLabel(REJECT_REASON_FIELD.name),
              ]),
              reason,
              reasonError,
            ])
          : null,
        el("div", { class: "run-actions" }, [confirmButton, cancelButton]),
      ],
    );
    // Escape leaves the confirmation exactly like the cancel button, so the
    // command is never one stray keystroke away from being sent.
    region.addEventListener("keydown", (event) => {
      if (!busy && /** @type {KeyboardEvent} */ (event).key === "Escape") {
        event.preventDefault();
        cancelPending(view.documentId);
      }
    });
    return region;
  }

  /**
   * @param {DocumentView} view
   * @returns {HTMLElement}
   */
  function actionsRegion(view) {
    if (pending !== null && pending.documentId === view.documentId) {
      return confirmationRegion(view, pending.action);
    }
    if (view.actions.length === 0) {
      return el("p", { class: "document-actions-note" }, [
        /** @type {string} */ (view.actionsNote),
      ]);
    }
    return el(
      "div",
      { class: "document-actions", role: "group", "aria-label": "Comandos do ciclo" },
      view.actions.map((action) => {
        const button = el(
          "button",
          {
            type: "button",
            class: "button button-quiet",
            "data-action": action.name,
            "data-document": view.documentId,
          },
          [action.label],
        );
        button.addEventListener("click", () => {
          if (busy) {
            return;
          }
          pending = { documentId: view.documentId, action };
          renderList();
          announce(`${action.label}: confirme ou cancele para continuar.`);
          focusPending();
        });
        return button;
      }),
    );
  }

  /**
   * @param {DocumentView} view
   * @returns {HTMLElement}
   */
  function card(view) {
    const confirming = pending !== null && pending.documentId === view.documentId;
    const readButton = el(
      "button",
      {
        type: "button",
        class: "button button-quiet",
        "data-read": view.documentId,
        disabled: confirming,
      },
      ["Ver estado atual"],
    );
    readButton.addEventListener("click", () => {
      if (!busy && !confirming) {
        void readOne(view);
      }
    });

    return el(
      "article",
      { class: "document-card", "data-tone": view.tone, "data-card": view.documentId },
      [
        el("header", { class: "document-head" }, [
          el("span", { class: "document-mark" }, [documentMark(view.status)]),
          el(
            "h4",
            {
              class: "document-title",
              tabindex: "-1",
              "data-card-heading": view.documentId,
            },
            [view.filename],
          ),
          el("span", { class: "document-state", "data-status": view.status }, [
            view.statusLabel,
          ]),
        ]),
        el("p", { class: "document-statement" }, [view.statement]),
        el("dl", { class: "document-facts" }, [
          definition("Identificador", view.documentId, null),
          definition("Última atualização", view.updatedAt, null),
          definition(
            view.currency.label,
            view.currency.supersededBy ?? "—",
            view.currency.explanation,
          ),
          ...view.metadata.map((entry) =>
            definition(entry.label, entry.value, entry.note),
          ),
        ]),
        view.decision === null
          ? null
          : el("div", { class: "document-decision" }, [
              el("p", { class: "block-label" }, [view.decision.label]),
              el("p", {}, [view.decision.text]),
            ]),
        view.failure === null
          ? null
          : el("div", { class: "document-failure" }, [
              el("p", { class: "block-label" }, ["Falha do processamento"]),
              el("p", { class: "mono" }, [view.failure.code]),
              el("p", {}, [view.failure.message]),
            ]),
        el("div", { class: "document-commands" }, [readButton, actionsRegion(view)]),
      ],
    );
  }

  /**
   * @returns {void}
   */
  function renderList() {
    clear(listHost);
    listHost.setAttribute("aria-busy", listLoading ? "true" : "false");
    if (listLoading && documents.length === 0) {
      countLabel.textContent = "Carregando documentos.";
      listHost.append(
        el("div", { class: "documents-loading", role: "status" }, [
          el("p", { class: "loading-kicker" }, ["Consultando a API"]),
          el("p", { class: "loading-title" }, ["Lendo o ciclo documental"]),
          el("div", { class: "skeleton" }, [
            el("span", { class: "skeleton-line skeleton-wide" }, []),
            el("span", { class: "skeleton-line" }, []),
            el("span", { class: "skeleton-line skeleton-short" }, []),
          ]),
        ]),
      );
    } else if (listFailure !== null) {
      countLabel.textContent = "A lista não pôde ser lida.";
      listHost.append(
        el("p", { class: "documents-empty" }, [
          "Nenhum documento é exibido enquanto a leitura da lista falhar.",
        ]),
      );
    } else {
      const list = presentDocumentList(documents);
      countLabel.textContent = listLoading
        ? `Atualizando ${String(list.total)} documento(s).`
        : list.empty
          ? "Nenhum documento registrado."
          : `${String(list.total)} documento(s) no ciclo.`;
      if (list.empty) {
        listHost.append(el("p", { class: "documents-empty" }, [list.emptyMessage]));
      } else {
        listHost.append(...list.items.map(card));
      }
    }
    setInteractiveDisabled(host, busy);
  }

  /**
   * @param {object} [settings]
   * @param {boolean} [settings.silent]
   * @returns {Promise<ListLoadResult>}
   */
  async function loadDocuments(settings = {}) {
    listLoading = true;
    renderList();
    if (settings.silent !== true) {
      announce("Lendo a lista de documentos.");
    }
    const output = await client.listDocuments();
    listLoading = false;
    if (!output.ok) {
      listFailure = presentDocumentFailure(output.failure);
      documents = Object.freeze([]);
      renderList();
      return { ok: false, failure: listFailure };
    }
    listFailure = null;
    documents = output.value;
    renderList();
    if (settings.silent !== true) {
      announce(
        documents.length === 0
          ? "A API não retornou nenhum documento."
          : `${String(documents.length)} documento(s) lidos da API.`,
      );
    }
    return { ok: true };
  }

  /**
   * @returns {Promise<void>}
   */
  async function refresh() {
    if (busy) {
      return;
    }
    if (pending !== null) {
      pending = null;
      renderList();
    }
    setBusy(true);
    try {
      const result = await loadDocuments();
      if (!result.ok) {
        reportFailure(result.failure);
      }
    } finally {
      setBusy(false);
    }
  }

  /**
   * @param {readonly ValidationIssue[]} issues
   * @returns {void}
   */
  function showRegisterIssues(issues) {
    for (const slot of registerErrors.values()) {
      slot.textContent = "";
    }
    for (const input of registerInputs.values()) {
      input.removeAttribute("aria-invalid");
    }
    for (const issue of issues) {
      const slot = registerErrors.get(issue.field);
      if (slot !== undefined) {
        slot.textContent = issue.message;
      }
      registerInputs.get(issue.field)?.setAttribute("aria-invalid", "true");
    }
    const first = issues[0];
    if (first !== undefined) {
      registerInputs.get(first.field)?.focus();
    }
  }

  /**
   * @returns {Promise<void>}
   */
  async function register() {
    /** @type {Record<string, string>} */
    const values = {};
    for (const [name, input] of registerInputs) {
      values[name] = input.value;
    }
    const built = buildRegisterRequest(values);
    if (!built.ok) {
      showRegisterIssues(built.issues);
      announce(
        `O registro não foi enviado: ${String(built.issues.length)} campo(s) precisam de correção.`,
      );
      return;
    }
    showRegisterIssues([]);
    if (pending !== null) {
      pending = null;
      renderList();
    }
    setBusy(true);
    announce("Registrando os metadados na API.");
    /** @type {string | null} */
    let registeredDocumentId = null;
    try {
      const receipt = await client.registerDocument(built.request);
      if (!receipt.ok) {
        reportFailure(presentDocumentFailure(receipt.failure));
        return;
      }
      registeredDocumentId = receipt.value.document_id;
      // The receipt repeats the original command of this identity, so it does
      // not prove the current state. Read it back before describing that state.
      const current = await client.getDocument(receipt.value.document_id);
      if (!current.ok) {
        const failed = presentDocumentFailure(current.failure);
        const confirmation =
          "Registro de metadados confirmado pela API. O estado atual e a lista " +
          "ainda não puderam ser confirmados.";
        renderFeedback(failed, confirmation);
        announce(`Registro confirmado, mas o estado atual não pôde ser lido. ${failed.nextStep}`);
        return;
      }
      const message = describeRegistration(current.value);
      const refreshed = await loadDocuments({ silent: true });
      if (!refreshed.ok) {
        renderFeedback(refreshed.failure, message);
        announce(
          `Registro confirmado, mas a lista não pôde ser atualizada. ` +
            refreshed.failure.nextStep,
        );
        return;
      }
      renderFeedback(null, message);
      announce(message);
    } finally {
      setBusy(false);
      if (registeredDocumentId !== null) {
        focusCard(registeredDocumentId);
      }
    }
  }

  /**
   * @returns {HTMLElement}
   */
  function buildRegisterForm() {
    const rows = REGISTER_INPUTS.map((field) => {
      const inputId = `document-${field.name}`;
      const errorId = `${inputId}-error`;
      const hintId = `${inputId}-hint`;
      const hint = documentFieldHint(field.name);
      const input = /** @type {HTMLInputElement} */ (
        el("input", {
          id: inputId,
          name: field.name,
          class: "field-input",
          type: "text",
          inputmode: field.node.kind === "integer" ? "numeric" : "text",
          autocomplete: "off",
          spellcheck: "false",
          "aria-describedby": hint === null ? errorId : `${hintId} ${errorId}`,
          "data-register": field.name,
        })
      );
      const error = el("p", {
        class: "field-error",
        id: errorId,
        "data-error-for": field.name,
      });
      registerInputs.set(field.name, input);
      registerErrors.set(field.name, error);
      input.addEventListener("input", () => {
        input.removeAttribute("aria-invalid");
        error.textContent = "";
      });
      return el("div", { class: "field" }, [
        el("label", { class: "field-label", for: inputId }, [
          documentFieldLabel(field.name),
        ]),
        input,
        hint === null ? null : el("p", { class: "field-hint", id: hintId }, [hint]),
        error,
      ]);
    });

    for (const example of SYNTHETIC_DOCUMENT_EXAMPLES) {
      exampleSelect.append(el("option", { value: example.name }, [example.summary]));
    }
    exampleSelect.addEventListener("change", () => {
      const values = syntheticRegisterValues(exampleSelect.value);
      if (values === null) {
        return;
      }
      for (const [name, input] of registerInputs) {
        input.value = values[name] ?? "";
      }
      showRegisterIssues([]);
      announce("Exemplo sintético do contrato carregado nos campos de registro.");
    });

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!busy) {
        void register();
      }
    });

    form.append(
      el("h3", { class: "block-label" }, ["Registrar metadados de um PDF"]),
      el("p", { class: "documents-note" }, [REGISTER_NOTE]),
      el("div", { class: "import-row" }, [
        el("label", { class: "field-label", for: "document-example" }, [
          "Exemplo sintético do contrato",
        ]),
        exampleSelect,
      ]),
      el("div", { class: "document-fields" }, rows),
      el("p", { class: "documents-note" }, [
        `${documentFieldLabel(REGISTER_MEDIA_TYPE.name)}: ${REGISTER_MEDIA_TYPE.value}, fixado pelo contrato v1.`,
      ]),
      el("div", { class: "run-actions" }, [submitButton]),
    );
    return form;
  }

  return {
    async start() {
      refreshButton.addEventListener("click", () => {
        if (!busy) {
          void refresh();
        }
      });
      host.replaceChildren(
        el("p", { class: "documents-lede" }, [LEDE]),
        buildRegisterForm(),
        el("div", { class: "documents-toolbar" }, [refreshButton, countLabel]),
        feedbackHost,
        listHost,
        statusRegion,
      );
      renderFeedback(null, null);
      await refresh();
    },
    refresh,
  };
}
