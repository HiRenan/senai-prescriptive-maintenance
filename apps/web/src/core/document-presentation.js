import { DOCUMENT_VARIANTS } from "../generated/document-contract.js";
import { formatMeasurement, formatTimestamp } from "./format.js";
import { documentFieldLabel } from "./document-registration.js";

/**
 * @typedef {import("../generated/document-contract.js").DocumentResponse} DocumentResponse
 * @typedef {import("../generated/document-contract.js").DocumentStatus} DocumentStatus
 * @typedef {import("../api/document-client.js").DocumentFailure} DocumentFailure
 */

/**
 * Which command each state accepts.
 *
 * The contract publishes the seven states and the five routes, but not the
 * transitions between them; those live in the lifecycle the API documents. The
 * policy is stated once, here, so the interface never offers a command the
 * current state cannot accept. A state the table does not cover offers nothing.
 */
export const DOCUMENT_ACTION_POLICY = Object.freeze({
  approve: Object.freeze(["pending_approval"]),
  reject: Object.freeze(["pending_approval"]),
  reprocess: Object.freeze(["rejected", "failed"]),
});

/**
 * @typedef {object} DocumentAction
 * @property {"approve" | "reject" | "reprocess"} name
 * @property {string} label
 * @property {string} confirmation
 * @property {string} confirmLabel
 * @property {boolean} needsReason
 */

const ACTIONS = Object.freeze([
  Object.freeze({
    name: /** @type {const} */ ("approve"),
    label: "Aprovar",
    confirmation:
      "Aprovar torna esta versão vigente e elegível para citação na análise. Confirme para registrar a decisão.",
    confirmLabel: "Confirmar aprovação",
    needsReason: false,
  }),
  Object.freeze({
    name: /** @type {const} */ ("reject"),
    label: "Rejeitar",
    confirmation:
      "Rejeitar registra uma decisão com motivo obrigatório e mantém a versão fora das citações.",
    confirmLabel: "Confirmar rejeição",
    needsReason: true,
  }),
  Object.freeze({
    name: /** @type {const} */ ("reprocess"),
    label: "Reprocessar",
    confirmation:
      "Reprocessar reinicia o processamento desta versão. Nenhuma aprovação é concedida por isso.",
    confirmLabel: "Confirmar reprocessamento",
    needsReason: false,
  }),
]);

const STATUS_COPY = Object.freeze({
  received: {
    tone: "degraded",
    label: "Recebido",
    statement:
      "Os metadados foram registrados. O processamento desta versão ainda não começou.",
  },
  processing: {
    tone: "degraded",
    label: "Em processamento",
    statement:
      "As etapas de extração e indexação estão em andamento. Nenhuma decisão foi tomada.",
  },
  pending_approval: {
    tone: "withheld",
    label: "Aguardando aprovação",
    statement:
      "O processamento terminou e a versão aguarda decisão humana. Ainda não sustenta citação.",
  },
  approved: {
    tone: "settled",
    label: "Aprovado",
    statement:
      "A decisão de aprovação está registrada e esta versão pode sustentar citações na análise.",
  },
  rejected: {
    tone: "outside",
    label: "Rejeitado",
    statement:
      "A decisão de rejeição está registrada com motivo. Esta versão não sustenta citação.",
  },
  failed: {
    tone: "failed",
    label: "Falhou",
    statement:
      "Uma etapa do processamento falhou. Esta versão não sustenta citação enquanto não for reprocessada.",
  },
  superseded: {
    tone: "degraded",
    label: "Substituído",
    statement:
      "Uma versão aprovada mais nova ocupou o lugar desta. Ela permanece no histórico, sem vigência.",
  },
});

const CURRENCY_COPY = Object.freeze({
  current: {
    label: "Vigente",
    explanation: "Nenhuma versão mais nova substituiu esta.",
  },
  superseded: {
    label: "Sem vigência",
    explanation: "O contrato aponta a versão que ocupou o lugar desta.",
  },
  not_current: {
    label: "Sem vigência",
    explanation: "Só uma versão aprovada e não substituída é vigente.",
  },
});

const FAILURE_COPY = Object.freeze({
  network: {
    title: "A API não respondeu",
    statement: "O navegador não conseguiu falar com o ciclo documental.",
    nextStep: "Confirme que a API está em execução e atualize a lista.",
  },
  timeout: {
    title: "O ciclo documental excedeu o tempo limite",
    statement: "A API não respondeu dentro do tempo aceito pelo painel.",
    nextStep: "Atualize a lista; se repetir, verifique a saúde da API.",
  },
  refused: {
    title: "O painel não enviou a requisição",
    statement: "Os valores informados não satisfazem o contrato v1.",
    nextStep: "Corrija os campos apontados e envie de novo.",
  },
  validation: {
    title: "A API recusou a requisição",
    statement: "O corpo enviado não passou na validação do contrato v1.",
    nextStep: "Corrija os campos apontados abaixo e envie de novo.",
  },
  missing: {
    title: "Documento não encontrado",
    statement: "A API não conhece este identificador.",
    nextStep: "Atualize a lista para ver os documentos que existem agora.",
  },
  conflict: {
    title: "O comando conflita com o estado atual",
    statement:
      "A API recusou porque o estado armazenado não aceita este comando, ou porque os metadados divergem de um registro existente.",
    nextStep: "Atualize o documento e confira o estado atual antes de repetir a ação.",
  },
  unavailable: {
    title: "O ciclo documental está indisponível",
    statement: "A API declarou indisponibilidade temporária e nada foi alterado.",
    nextStep: "Aguarde e tente de novo. Nenhuma decisão foi registrada.",
  },
  unexpected: {
    title: "Resposta inesperada da API",
    statement: "A API respondeu com um status fora do contrato v1.",
    nextStep: "Registre o status recebido e verifique a versão da API publicada.",
  },
  malformed: {
    title: "Resposta fora do contrato",
    statement: "O corpo devolvido pela API não corresponde ao contrato v1.",
    nextStep: "Verifique se a API publicada é a v1 e atualize a lista.",
  },
});

/**
 * @typedef {object} DocumentView
 * @property {string} documentId
 * @property {string} filename
 * @property {DocumentStatus} status
 * @property {string} statusLabel
 * @property {string} tone
 * @property {string} statement
 * @property {readonly {label: string, value: string, note: string | null}[]} metadata
 * @property {string} updatedAt
 * @property {{state: string, label: string, explanation: string,
 *   supersededBy: string | null}} currency
 * @property {{label: string, text: string} | null} decision
 * @property {{code: string, message: string} | null} failure
 * @property {readonly DocumentAction[]} actions
 * @property {string | null} actionsNote
 */

/**
 * @typedef {object} DocumentFailureView
 * @property {string} title
 * @property {string} statement
 * @property {string} nextStep
 * @property {number | null} status
 * @property {string | null} detail
 * @property {readonly {label: string, code: string}[]} issues
 */

/**
 * List the commands the current state accepts, in a stable order.
 *
 * @param {string} status
 * @returns {readonly DocumentAction[]}
 */
export function documentActions(status) {
  return Object.freeze(
    ACTIONS.filter((action) => DOCUMENT_ACTION_POLICY[action.name].includes(status)),
  );
}

/**
 * @param {DocumentResponse} document
 * @returns {{state: string, label: string, explanation: string,
 *   supersededBy: string | null}}
 */
function resolveCurrency(document) {
  if (document.superseded_by_document_id !== null) {
    return {
      state: "superseded",
      ...CURRENCY_COPY.superseded,
      supersededBy: document.superseded_by_document_id,
    };
  }
  const state = document.status === "approved" ? "current" : "not_current";
  return {
    state,
    ...CURRENCY_COPY[/** @type {"current" | "not_current"} */ (state)],
    supersededBy: null,
  };
}

/**
 * Build the auditable view of one document state.
 *
 * Nothing is invented: the contract publishes no version number and no
 * processing instant, so the view shows `updated_at` as the last update and
 * reads currency from the status and from `superseded_by_document_id`.
 *
 * @param {DocumentResponse} document
 * @returns {DocumentView}
 */
export function presentDocument(document) {
  const variant = DOCUMENT_VARIANTS.find((entry) => entry.status === document.status);
  const copy = STATUS_COPY[document.status];
  const actions = documentActions(document.status);
  const note = document.decision_note;
  const failure = document.failure;
  return {
    documentId: document.document_id,
    filename: document.filename,
    status: document.status,
    statusLabel: copy.label,
    tone: copy.tone,
    statement: copy.statement,
    metadata: Object.freeze([
      {
        label: documentFieldLabel("media_type"),
        value: document.media_type,
        note: null,
      },
      {
        label: documentFieldLabel("size_bytes"),
        value: formatMeasurement(document.size_bytes),
        note: "Declarado no registro; a API v1 não recebe os bytes.",
      },
      {
        label: documentFieldLabel("sha256"),
        value: document.sha256,
        note: "Declarado no registro; a API v1 não recalcula o hash.",
      },
      {
        label: "Primeiro registro",
        value: formatTimestamp(document.created_at),
        note: null,
      },
    ]),
    updatedAt: formatTimestamp(document.updated_at),
    currency: resolveCurrency(document),
    decision:
      typeof note === "string" && note.length > 0
        ? {
            label:
              variant !== undefined && variant.requiresDecisionNote
                ? "Motivo da rejeição"
                : "Nota da decisão",
            text: note,
          }
        : null,
    failure:
      failure === null ? null : { code: failure.code, message: failure.message },
    actions,
    actionsNote:
      actions.length === 0
        ? "Nenhum comando do ciclo se aplica a este estado."
        : null,
  };
}

/**
 * Build the view of the listing, with an empty state that claims nothing.
 *
 * @param {readonly DocumentResponse[]} documents
 * @returns {{ empty: boolean, emptyMessage: string, total: number,
 *   items: readonly DocumentView[] }}
 */
export function presentDocumentList(documents) {
  return {
    empty: documents.length === 0,
    emptyMessage:
      "A API não retornou nenhum documento. Registre os metadados de um PDF para começar o ciclo.",
    total: documents.length,
    items: Object.freeze(documents.map(presentDocument)),
  };
}

/**
 * Build the view shown when a documental command produced no contract result.
 *
 * @param {DocumentFailure} failure
 * @returns {DocumentFailureView}
 */
export function presentDocumentFailure(failure) {
  const copy = FAILURE_COPY[failure.kind];
  return {
    title: copy.title,
    statement: copy.statement,
    nextStep: copy.nextStep,
    status: failure.status,
    detail: failure.detail,
    issues: Object.freeze(
      failure.issues.map((issue) => ({
        label: documentFieldLabel(issue.field),
        code: issue.code,
      })),
    ),
  };
}

/**
 * Describe the outcome of a registration without claiming what the contract
 * does not report.
 *
 * `POST /documents` answers with the immutable receipt of the first command
 * with the same identity, so a repeat is answered exactly like the original.
 * The panel cannot tell the two apart and must not guess: it confirms the
 * registration and states the current state read back from the API.
 *
 * @param {DocumentResponse} current
 * @returns {string}
 */
export function describeRegistration(current) {
  const copy = STATUS_COPY[current.status];
  return (
    "Registro de metadados confirmado pela API. " +
    `O estado atual deste documento é "${copy.label}". ` +
    "Registrar de novo os mesmos metadados confirma o mesmo registro, sem criar outro."
  );
}
